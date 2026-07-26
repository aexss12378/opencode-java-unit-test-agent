import com.sun.source.tree.AnnotationTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.ModifiersTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.DocTrees;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermission;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

/**
 * 只在既有 Java 宣告前新增 Javadoc 的受信任後端。
 *
 * <p>輸入協定：
 *
 * <ol>
 *   <li>第一行：Base64 編碼的專案相對路徑
 *   <li>第二行：新增數量
 *   <li>後續每行：目標行號、Tab、Base64 編碼的 Javadoc 內文
 * </ol>
 */
public final class JavadocEditBackend {

    private static final int MAX_FILE_BYTES = 1_000_000;
    private static final int MAX_POM_BYTES = 1_000_000;
    private static final int MAX_JAVADOC_BYTES = 50_000;
    private static final int MAX_ADDITIONS = 100;
    private static final Pattern UNICODE_ESCAPE = Pattern.compile("\\\\u+");
    private static final Pattern MAVEN_PROPERTY = Pattern.compile("^\\$\\{([^}]+)}$");
    private static final Set<Integer> SUPPORTED_JAVA_RELEASES = Set.of(8, 17, 21);

    private JavadocEditBackend() {}

    public static void main(String[] args) {
        try {
            Path repository = repository(args);
            Request request = readRequest();
            Result result = apply(repository, request);
            System.out.println(successJson(result));
        } catch (RequestError error) {
            System.out.println(errorJson(
                    "blocked",
                    error.code(),
                    error.getMessage(),
                    error.retryable()
            ));
        } catch (Exception error) {
            System.out.println(errorJson(
                    "tool-error",
                    "INTERNAL_ERROR",
                    error.getClass().getSimpleName() + ": " + safeMessage(error),
                    false
            ));
        }
    }

    private static Path repository(String[] args) throws IOException {
        if (args.length != 2 || !"--repo".equals(args[0])) {
            throw new RequestError("後端參數必須是 --repo <目前工作目錄>");
        }
        Path repository = Path.of(args[1]).toRealPath();
        if (!repository.equals(Path.of("").toRealPath())) {
            throw new RequestError("--repo 必須指向目前工作目錄");
        }

        Path current = repository;
        for (String part : List.of("src", "main", "java")) {
            current = current.resolve(part);
            if (Files.isSymbolicLink(current)) {
                throw new RequestError(
                        "SYMLINK_NOT_ALLOWED",
                        "src/main/java 路徑不得包含符號連結",
                        false
                );
            }
            if (!Files.isDirectory(current, LinkOption.NOFOLLOW_LINKS)) {
                throw new RequestError("專案缺少既有的 src/main/java 目錄");
            }
        }
        return repository;
    }

    private static Request readRequest() throws IOException {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(System.in, StandardCharsets.UTF_8)
        );
        String encodedPath = requiredLine(reader, "缺少 Java 檔案路徑");
        String rawCount = requiredLine(reader, "缺少新增數量");

        String path = decode(encodedPath, "Java 檔案路徑");
        int count;
        try {
            count = Integer.parseInt(rawCount);
        } catch (NumberFormatException error) {
            throw new RequestError("新增數量必須是整數");
        }
        if (count < 1 || count > MAX_ADDITIONS) {
            throw new RequestError("每次新增數量必須介於 1 到 " + MAX_ADDITIONS);
        }

        List<Addition> additions = new ArrayList<>();
        for (int index = 0; index < count; index++) {
            String line = requiredLine(reader, "Javadoc 新增資料不足");
            int separator = line.indexOf('\t');
            if (separator <= 0 || separator == line.length() - 1) {
                throw new RequestError("Javadoc 新增資料格式錯誤");
            }
            int targetLine;
            try {
                targetLine = Integer.parseInt(line.substring(0, separator));
            } catch (NumberFormatException error) {
                throw new RequestError("目標行號必須是整數");
            }
            if (targetLine < 1) {
                throw new RequestError("目標行號必須大於 0");
            }
            String javadoc = decode(line.substring(separator + 1), "Javadoc 內文");
            additions.add(new Addition(targetLine, normalizeJavadoc(javadoc)));
        }

        String extra;
        while ((extra = reader.readLine()) != null) {
            if (!extra.isBlank()) {
                throw new RequestError("輸入包含未預期的額外資料");
            }
        }
        return new Request(path, additions);
    }

    private static Result apply(Path repository, Request request) throws Exception {
        int javaRelease = javaRelease(repository);
        Path target = sourceFile(repository, request.path());
        byte[] beforeBytes = Files.readAllBytes(target);
        if (beforeBytes.length > MAX_FILE_BYTES) {
            throw new RequestError("Java 檔案不得超過 " + MAX_FILE_BYTES + " bytes");
        }
        String before = decodeUtf8(beforeBytes);
        Inspection beforeInspection = inspect(request.path(), before, javaRelease);

        Set<Integer> requestedLines = new HashSet<>();
        List<Insertion> insertions = new ArrayList<>();
        String newline = before.contains("\r\n") ? "\r\n" : "\n";

        for (Addition addition : request.additions()) {
            if (!requestedLines.add(addition.targetLine())) {
                throw new RequestError(
                        "DUPLICATE_TARGET",
                        "同一個宣告不得重複新增 Javadoc：第 "
                                + addition.targetLine() + " 行",
                        true
                );
            }

            List<Declaration> matches = beforeInspection.byLine().getOrDefault(
                    addition.targetLine(),
                    List.of()
            );
            if (matches.isEmpty()) {
                throw new RequestError(
                        "TARGET_NOT_DECLARATION",
                        "第 " + addition.targetLine()
                                + " 行不是可新增 Javadoc 的宣告起始行",
                        true
                );
            }
            if (matches.size() != 1) {
                throw new RequestError(
                        "AMBIGUOUS_DECLARATION",
                        "第 " + addition.targetLine()
                                + " 行包含多個宣告，第一版不處理這種寫法",
                        false
                );
            }

            Declaration declaration = matches.get(0);
            if (declaration.hasJavadoc()) {
                throw new RequestError(
                        "ALREADY_DOCUMENTED",
                        "第 " + addition.targetLine()
                                + " 行的宣告已經有 Javadoc；請移除此新增項目",
                        true
                );
            }

            int lineStart = lineStart(before, declaration.start());
            String indentation = before.substring(lineStart, declaration.start());
            if (!indentation.chars().allMatch(character -> character == ' ' || character == '\t')) {
                throw new RequestError(
                        "INLINE_DECLARATION",
                        "第 " + addition.targetLine()
                                + " 行的宣告不是獨立起始行，第一版不處理行內宣告",
                        false
                );
            }

            String rendered = renderJavadoc(indentation, addition.javadoc(), newline);
            insertions.add(new Insertion(
                    lineStart,
                    rendered,
                    declaration.key(),
                    addition.targetLine()
            ));
        }

        insertions.sort(Comparator.comparingInt(Insertion::offset).reversed());
        StringBuilder candidate = new StringBuilder(before);
        for (Insertion insertion : insertions) {
            candidate.insert(insertion.offset(), insertion.text());
        }
        String after = candidate.toString();

        Inspection afterInspection = inspect(request.path(), after, javaRelease);
        if (!beforeInspection.keys().equals(afterInspection.keys())) {
            throw new RequestError(
                    "NON_JAVADOC_CHANGE",
                    "偵測到 Javadoc 以外的 Java 宣告結構變更",
                    true
            );
        }
        for (Insertion insertion : insertions) {
            List<Declaration> declarations = afterInspection.byKey().getOrDefault(
                    insertion.declarationKey(),
                    List.of()
            );
            if (declarations.size() != 1 || !declarations.get(0).hasJavadoc()) {
                throw new RequestError(
                        "INVALID_JAVADOC_CHANGE",
                        "第 " + insertion.originalLine()
                                + " 行新增的內容沒有成為該宣告的 Javadoc",
                        true
                );
            }
        }

        byte[] currentBytes = Files.readAllBytes(target);
        if (!Arrays.equals(beforeBytes, currentBytes)) {
            throw new RequestError(
                    "SOURCE_CHANGED",
                    "Java 檔案在驗證期間已被其他程序修改",
                    true
            );
        }

        byte[] afterBytes = after.getBytes(StandardCharsets.UTF_8);
        atomicWrite(target, afterBytes);
        return new Result(
                request.path(),
                javaRelease,
                request.additions().size(),
                requestedLines.stream().sorted().toList()
        );
    }

    private static int javaRelease(Path repository) throws Exception {
        Path pom = repository.resolve("pom.xml");
        if (Files.isSymbolicLink(pom)
                || !Files.isRegularFile(pom, LinkOption.NOFOLLOW_LINKS)) {
            throw new RequestError(
                    "POM_NOT_FOUND",
                    "專案根目錄必須有既有且非符號連結的 pom.xml",
                    false
            );
        }
        byte[] pomBytes = Files.readAllBytes(pom);
        if (pomBytes.length > MAX_POM_BYTES) {
            throw new RequestError("pom.xml 不得超過 " + MAX_POM_BYTES + " bytes");
        }

        Document document;
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            factory.setFeature(
                    "http://apache.org/xml/features/disallow-doctype-decl",
                    true
            );
            factory.setFeature(
                    "http://xml.org/sax/features/external-general-entities",
                    false
            );
            factory.setFeature(
                    "http://xml.org/sax/features/external-parameter-entities",
                    false
            );
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            document = factory.newDocumentBuilder().parse(new ByteArrayInputStream(pomBytes));
        } catch (Exception error) {
            throw new RequestError(
                    "POM_PARSE_ERROR",
                    "無法安全解析 pom.xml：" + safeMessage(error),
                    false
            );
        }

        Map<String, String> properties = mavenProperties(document.getDocumentElement());
        String configured = firstConfigured(
                properties.get("maven.compiler.release"),
                properties.get("java.version"),
                properties.get("maven.compiler.source")
        );
        if (configured == null) {
            throw new RequestError(
                    "JAVA_RELEASE_NOT_FOUND",
                    "pom.xml 必須明確設定 maven.compiler.release、java.version"
                            + " 或 maven.compiler.source",
                    false
            );
        }

        String resolved = resolveMavenProperty(configured, properties, new HashSet<>());
        int release;
        try {
            release = "1.8".equals(resolved)
                    ? 8
                    : Integer.parseInt(resolved);
        } catch (NumberFormatException error) {
            throw new RequestError(
                    "JAVA_RELEASE_INVALID",
                    "pom.xml 的 Java 版本不是有效整數：" + resolved,
                    false
            );
        }
        if (!SUPPORTED_JAVA_RELEASES.contains(release)) {
            throw new RequestError(
                    "JAVA_RELEASE_NOT_SUPPORTED",
                    "目前只支援 Java 8、17、21，pom.xml 設定為 Java " + release,
                    false
            );
        }
        return release;
    }

    private static Map<String, String> mavenProperties(Element project) {
        Map<String, String> properties = new HashMap<>();
        for (Element child : childElements(project)) {
            if (!"properties".equals(localName(child))) {
                continue;
            }
            for (Element property : childElements(child)) {
                properties.put(localName(property), property.getTextContent().trim());
            }
        }
        return properties;
    }

    private static List<Element> childElements(Element parent) {
        List<Element> children = new ArrayList<>();
        NodeList nodes = parent.getChildNodes();
        for (int index = 0; index < nodes.getLength(); index++) {
            Node node = nodes.item(index);
            if (node instanceof Element element) {
                children.add(element);
            }
        }
        return children;
    }

    private static String localName(Element element) {
        return element.getLocalName() == null
                ? element.getTagName()
                : element.getLocalName();
    }

    private static String firstConfigured(String... values) {
        return Arrays.stream(values)
                .filter(value -> value != null && !value.isBlank())
                .findFirst()
                .orElse(null);
    }

    private static String resolveMavenProperty(
            String value,
            Map<String, String> properties,
            Set<String> resolving
    ) {
        var matcher = MAVEN_PROPERTY.matcher(value.trim());
        if (!matcher.matches()) {
            return value.trim();
        }
        String name = matcher.group(1);
        String configured = properties.get(name);
        if (configured == null || !resolving.add(name)) {
            throw new RequestError(
                    "JAVA_RELEASE_INVALID",
                    "無法解析 pom.xml 的 Java 版本屬性：" + value,
                    false
            );
        }
        return resolveMavenProperty(configured, properties, resolving);
    }

    private static Path sourceFile(Path repository, String rawPath) throws IOException {
        if (rawPath.isBlank()
                || rawPath.startsWith("/")
                || rawPath.contains("\\")
                || !rawPath.startsWith("src/main/java/")
                || !rawPath.endsWith(".java")) {
            throw new RequestError(
                    "PATH_NOT_ALLOWED",
                    "只能修改 src/main/java/** 下的既有 .java 檔案",
                    false
            );
        }
        String[] parts = rawPath.split("/", -1);
        for (String part : parts) {
            if (part.isEmpty() || ".".equals(part) || "..".equals(part)) {
                throw new RequestError(
                        "INVALID_PATH",
                        "Java 檔案路徑格式不合法",
                        false
                );
            }
        }

        Path target = repository.resolve(rawPath).normalize();
        Path lexicalSourceRoot = repository.resolve("src/main/java").normalize();
        Path sourceRoot = lexicalSourceRoot.toRealPath();
        if (!target.startsWith(lexicalSourceRoot)) {
            throw new RequestError(
                    "PATH_NOT_ALLOWED",
                    "Java 檔案路徑離開 src/main/java",
                    false
            );
        }
        Path current = lexicalSourceRoot;
        for (Path part : lexicalSourceRoot.relativize(target)) {
            current = current.resolve(part);
            if (Files.isSymbolicLink(current)) {
                throw new RequestError(
                        "SYMLINK_NOT_ALLOWED",
                        "Java 檔案路徑不得包含符號連結",
                        false
                );
            }
        }
        if (Files.isSymbolicLink(target)
                || !Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)) {
            throw new RequestError(
                    "INVALID_TARGET",
                    "目標必須是既有且非符號連結的 Java 一般檔案",
                    false
            );
        }
        Path realTarget = target.toRealPath();
        if (!realTarget.startsWith(sourceRoot)) {
            throw new RequestError(
                    "PATH_NOT_ALLOWED",
                    "Java 檔案真實路徑離開 src/main/java",
                    false
            );
        }
        return target;
    }

    private static Inspection inspect(String path, String source, int javaRelease) throws Exception {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            throw new RequestError("目前 Java 環境缺少 JDK 編譯器");
        }

        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        try (StandardJavaFileManager fileManager = compiler.getStandardFileManager(
                diagnostics,
                null,
                StandardCharsets.UTF_8
        )) {
            JavaFileObject sourceObject = new StringSource(path, source);
            JavacTask task = (JavacTask) compiler.getTask(
                    null,
                    fileManager,
                    diagnostics,
                    List.of("--release", String.valueOf(javaRelease), "-proc:none"),
                    null,
                    List.of(sourceObject)
            );
            Iterator<? extends CompilationUnitTree> iterator = task.parse().iterator();
            if (!iterator.hasNext()) {
                throw new RequestError("Java 解析器沒有產生語法樹");
            }
            CompilationUnitTree unit = iterator.next();
            if (iterator.hasNext()) {
                throw new RequestError("單一 Java 檔案產生了多個語法樹");
            }

            List<String> errors = diagnostics.getDiagnostics().stream()
                    .filter(diagnostic -> diagnostic.getKind() == Diagnostic.Kind.ERROR)
                    .map(JavadocEditBackend::diagnosticText)
                    .toList();
            if (!errors.isEmpty()) {
                throw new RequestError(
                        "SOURCE_PARSE_ERROR",
                        "Java 語法解析失敗：" + String.join("；", errors),
                        false
                );
            }

            DocTrees docTrees = DocTrees.instance(task);
            SourcePositions positions = docTrees.getSourcePositions();
            DeclarationScanner scanner = new DeclarationScanner(unit, docTrees, positions);
            scanner.scan(unit, null);
            return scanner.inspection();
        }
    }

    private static String diagnosticText(Diagnostic<? extends JavaFileObject> diagnostic) {
        String location = diagnostic.getLineNumber() > 0
                ? "第 " + diagnostic.getLineNumber() + " 行："
                : "";
        return location + diagnostic.getMessage(null);
    }

    private static String normalizeJavadoc(String value) {
        String normalized = value.replace("\r\n", "\n").replace('\r', '\n');
        while (normalized.startsWith("\n")) {
            normalized = normalized.substring(1);
        }
        while (normalized.endsWith("\n")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        if (normalized.isBlank()) {
            throw new RequestError("Javadoc 內文不得為空");
        }
        if (normalized.getBytes(StandardCharsets.UTF_8).length > MAX_JAVADOC_BYTES) {
            throw new RequestError("單一 Javadoc 內文不得超過 "
                    + MAX_JAVADOC_BYTES + " bytes");
        }
        if (normalized.indexOf('\0') >= 0
                || normalized.contains("*/")
                || UNICODE_ESCAPE.matcher(normalized).find()) {
            throw new RequestError(
                    "UNSAFE_JAVADOC_CONTENT",
                    "Javadoc 內文包含可能提早結束註解並改動 Java 程式碼的內容",
                    true
            );
        }
        return normalized;
    }

    private static String renderJavadoc(String indentation, String body, String newline) {
        StringBuilder rendered = new StringBuilder();
        rendered.append(indentation).append("/**").append(newline);
        for (String line : body.split("\n", -1)) {
            rendered.append(indentation).append(" *");
            if (!line.isEmpty()) {
                rendered.append(' ').append(line);
            }
            rendered.append(newline);
        }
        rendered.append(indentation).append(" */").append(newline);
        return rendered.toString();
    }

    private static int lineStart(String source, int offset) {
        int previousNewline = source.lastIndexOf('\n', Math.max(0, offset - 1));
        return previousNewline < 0 ? 0 : previousNewline + 1;
    }

    private static void atomicWrite(Path target, byte[] content) throws IOException {
        Path temporary = target.resolveSibling(
                "." + target.getFileName() + ".javadoc-edit-" + UUID.randomUUID()
        );
        try {
            Files.write(
                    temporary,
                    content,
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE
            );
            try {
                Set<PosixFilePermission> permissions = Files.getPosixFilePermissions(target);
                Files.setPosixFilePermissions(temporary, permissions);
            } catch (UnsupportedOperationException ignored) {
                // 非 POSIX 檔案系統不提供權限位元；內容安全檢查不受影響。
            }
            try {
                Files.move(
                        temporary,
                        target,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException error) {
                throw new RequestError("檔案系統不支援原子寫入，未修改 Java 檔案");
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private static String decodeUtf8(byte[] value) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(value))
                    .toString();
        } catch (CharacterCodingException error) {
            throw new RequestError("Java 檔案不是有效 UTF-8");
        }
    }

    private static String requiredLine(BufferedReader reader, String message) throws IOException {
        String line = reader.readLine();
        if (line == null) {
            throw new RequestError(message);
        }
        return line;
    }

    private static String decode(String encoded, String label) {
        try {
            return new String(Base64.getDecoder().decode(encoded), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException error) {
            throw new RequestError(label + "不是有效 Base64");
        }
    }

    private static String safeMessage(Exception error) {
        return error.getMessage() == null ? "沒有錯誤訊息" : error.getMessage();
    }

    private static String successJson(Result result) {
        String lines = result.lines().stream()
                .map(String::valueOf)
                .collect(Collectors.joining(","));
        return "{"
                + "\"status\":\"published\","
                + "\"path\":\"" + json(result.path()) + "\","
                + "\"java_release\":" + result.javaRelease() + ","
                + "\"added\":" + result.added() + ","
                + "\"target_lines\":[" + lines + "],"
                + "\"written\":true"
                + "}";
    }

    private static String errorJson(
            String status,
            String code,
            String message,
            boolean retryable
    ) {
        String guidance = retryable
                ? message
                        + "；沒有寫入任何檔案。請重新讀取該 Java 檔案，"
                        + "只修正 Javadoc 內文或目標行號後再提交"
                : message + "；沒有寫入任何檔案";
        return "{"
                + "\"status\":\"" + json(status) + "\","
                + "\"code\":\"" + json(code) + "\","
                + "\"message\":\"" + json(guidance) + "\","
                + "\"retryable\":" + retryable + ","
                + "\"written\":false"
                + "}";
    }

    private static String json(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> escaped.append("\\\"");
                case '\\' -> escaped.append("\\\\");
                case '\b' -> escaped.append("\\b");
                case '\f' -> escaped.append("\\f");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (character < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
                }
            }
        }
        return escaped.toString();
    }

    private record Request(String path, List<Addition> additions) {}

    private record Addition(int targetLine, String javadoc) {}

    private record Insertion(
            int offset,
            String text,
            String declarationKey,
            int originalLine
    ) {}

    private record Result(String path, int javaRelease, int added, List<Integer> lines) {}

    private record Declaration(
            String key,
            int line,
            int start,
            boolean hasJavadoc
    ) {}

    private record Inspection(
            List<String> keys,
            Map<Integer, List<Declaration>> byLine,
            Map<String, List<Declaration>> byKey
    ) {}

    private static final class RequestError extends RuntimeException {
        private static final long serialVersionUID = 1L;
        private final String code;
        private final boolean retryable;

        private RequestError(String message) {
            this("REQUEST_REJECTED", message, false);
        }

        private RequestError(String code, String message, boolean retryable) {
            super(message);
            this.code = code;
            this.retryable = retryable;
        }

        private String code() {
            return code;
        }

        private boolean retryable() {
            return retryable;
        }
    }

    private static final class StringSource extends SimpleJavaFileObject {
        private final String source;

        private StringSource(String path, String source) {
            super(URI.create("string:///" + path.replace(" ", "%20")), Kind.SOURCE);
            this.source = source;
        }

        @Override
        public CharSequence getCharContent(boolean ignoreEncodingErrors) {
            return source;
        }
    }

    private static final class DeclarationScanner extends TreePathScanner<Void, Void> {
        private final CompilationUnitTree unit;
        private final DocTrees docTrees;
        private final SourcePositions positions;
        private final Deque<String> owners = new ArrayDeque<>();
        private final List<Declaration> declarations = new ArrayList<>();

        private DeclarationScanner(
                CompilationUnitTree unit,
                DocTrees docTrees,
                SourcePositions positions
        ) {
            this.unit = unit;
            this.docTrees = docTrees;
            this.positions = positions;
        }

        private Inspection inspection() {
            List<String> keys = declarations.stream()
                    .map(Declaration::key)
                    .sorted()
                    .toList();
            Map<Integer, List<Declaration>> byLine = new HashMap<>();
            Map<String, List<Declaration>> byKey = new HashMap<>();
            for (Declaration declaration : declarations) {
                byLine.computeIfAbsent(declaration.line(), ignored -> new ArrayList<>())
                        .add(declaration);
                byKey.computeIfAbsent(declaration.key(), ignored -> new ArrayList<>())
                        .add(declaration);
            }
            return new Inspection(keys, byLine, byKey);
        }

        @Override
        public Void visitClass(ClassTree tree, Void unused) {
            String simpleName = tree.getSimpleName().toString();
            if (simpleName.isEmpty()) {
                return null;
            }
            String owner = owners.isEmpty()
                    ? simpleName
                    : owners.peek() + "." + simpleName;
            add("type:" + owner, tree, tree.getModifiers());

            owners.push(owner);
            for (Tree member : tree.getMembers()) {
                scan(member, unused);
            }
            owners.pop();
            return null;
        }

        @Override
        public Void visitMethod(MethodTree tree, Void unused) {
            if (owners.isEmpty()) {
                return null;
            }
            String parameters = tree.getParameters().stream()
                    .map(VariableTree::getType)
                    .map(String::valueOf)
                    .map(type -> type.replaceAll("\\s+", ""))
                    .collect(Collectors.joining(","));
            String name = tree.getReturnType() == null
                    ? "<init>"
                    : tree.getName().toString();
            add(owners.peek() + "#" + name + "(" + parameters + ")", tree, tree.getModifiers());
            return null;
        }

        @Override
        public Void visitVariable(VariableTree tree, Void unused) {
            if (!owners.isEmpty()) {
                add(owners.peek() + "#" + tree.getName(), tree, tree.getModifiers());
            }
            return null;
        }

        private void add(String key, Tree tree, ModifiersTree modifiers) {
            long start = positions.getStartPosition(unit, tree);
            for (AnnotationTree annotation : modifiers.getAnnotations()) {
                long annotationStart = positions.getStartPosition(unit, annotation);
                if (annotationStart >= 0 && (start < 0 || annotationStart < start)) {
                    start = annotationStart;
                }
            }
            if (start < 0 || start > Integer.MAX_VALUE) {
                throw new RequestError("無法取得 Java 宣告的來源位置：" + key);
            }
            TreePath path = getCurrentPath();
            boolean hasJavadoc = path != null && docTrees.getDocCommentTree(path) != null;
            int line = Math.toIntExact(unit.getLineMap().getLineNumber(start));
            declarations.add(new Declaration(key, line, (int) start, hasJavadoc));
        }
    }
}
