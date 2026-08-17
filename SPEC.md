# TECHNICAL SPECIFICATION (SPEC.md)
## Product CLI Tool `md-link-checker` (Markdown Link & Dead Code Checker)

### 1. Environment and Technology Stack
- Language & Exact Version Python 3.11.8 (strict; require modern syntax and complete type hints).
- Core Dependencies
  - `httpx` (asynchronous HTTP requests using `httpx.AsyncClient`).
  - `rich` (terminal UI rendering `Console`, `Table`, `Progress`, `Panel`).
- Python 3.11.8 Standard Modules `pathlib.Path`, `asyncio`, `re`, `urllib.parse`, `sys`.

---

### 2. Execution Flow and User Input (UX)
1. Interactive Path Prompt
   - Upon execution, the script must always prompt the user via standard input
     ```text
     Enter directory path to check (press Enter to scan current directory) 
     ```
2. Path Validation
   - If user input is empty (Enter key pressed), default to `Path.cwd()`.
   - Resolve the target path to an absolute path via `pathlib.Path(user_input).resolve()`.
   - Verify that the target path exists and is a valid directory via `path.exists()` and `path.is_dir()`.
   - If invalid, print a clear error message in red using `rich` and exit with status code `1` (`sys.exit(1)`).

---

### 3. Functional Requirements

#### 3.1. Recursive File Discovery
- Recursively discover all Markdown files matching `.md` (case-insensitive) across the entire directory tree using `Path.rglob(.md)`.
- Directory Exclusion Exclude and skip all files inside the following directories
  - `node_modules`, `.git`, `.venv`, `venv`, `dist`, `build`, `__pycache__`, `.idea`, `.vscode`.

#### 3.2. Markdown Link Extraction
- Extract targets from
  - Standard markdown links `[text](url)`
  - Images `![alt](url)`
  - Autolinks  Angle bracket links `http...`, `https...`, `file...`
- Ignored Targets
  - In-page anchors (e.g., `#section-title`).
  - Non-HTTPlocal schemes `mailto`, `tel`, `javascript`.
  - Any links located within multi-line code blocks (```` ```...``` ````) or inline code spans (`` `...` ``).

#### 3.3. Local File Path Resolution (CRITICAL)
- Relative Path Resolution
  - Relative paths (e.g., `.image.png`, `docssetup.md`, `..stylesmain.css`) must be resolved strictly relative to the directory containing the current `.md` file (`current_md_path.parent  clean_link`), never relative to the CLI invocation root.
- Anchor Stripping
  - If a link contains a file anchor (e.g., `.guide.md#installation`), strip the anchor (`#installation`) before verifying file existence on disk.
- `file` Scheme
  - Parse and decode percent-encoded characters using `urllib.parse.unquote()`, extract the local path, and verify physical existence.
- Local validation must be performed using `resolved_path.exists()`.

#### 3.4. Asynchronous Network Validation (HTTP  HTTPS)
1. Concurrency & Timeouts
   - Manage concurrent requests using `asyncio.Semaphore(15)`.
   - Set a strict per-request timeout of `5.0` seconds.
2. Request Headers
   - Include a desktop user-agent header
     `User-Agent Mozilla5.0 (Windows NT 10.0; Win64; x64) AppleWebKit537.36`
3. Request Strategy
   - Attempt an initial fast `HEAD` request.
   - If the server responds with status `403`, `405`, or `500`, retry using a `GET` request (stream headers, avoid downloading full response bodies).
4. Exception and Error Handling
   - Catch network and connection exceptions (`httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPError`).
   - Treat any HTTP status code `= 400` or connection failure as a broken link.

---

### 4. Terminal Interface & Reporting (`rich`)
1. Live Feedback Display an active spinner or progress indicator during scanning and link verification.
2. Results Table (`rich.table.Table`)
   - Columns `Source File`, `Link Target`, `Type (Local  HTTP)`, `Status`, `Details  Code`.
   - Color coding
     - 🟢 Green `200 OK`, `File exists`.
     - 🔴 Red `404 Not Found`, `File not found`, `Timeout`, `Connection Refused`.
3. Summary Panel (`rich.panel.Panel`)
   - Output overall metrics Total `.md` files scanned, total links found, valid links count, broken links count.
4. Exit Codes (CICD compatibility)
   - Return exit code `0` if all detected links are valid.
   - Return exit code `1` if at least one broken link is detected or if path validation fails.

---

### 5. LLM Implementation Prompt
1. Generate a single, production-ready script named `checker.py` targeted for Python 3.11.8.
2. Include the standard entry point `if __name__ == __main__ asyncio.run(main())`.
3. Ensure modular architecture, explicit type hinting, and descriptive comments on regex parsing, path resolution, and asynchronous request handling.