#!/usr/bin/env python3
"""
Markdown Link & Dead Code Checker (md-link-checker)
Target Python Version: 3.11.8

Recursively checks Markdown (.md) files in a target directory for broken local file
references and dead HTTP/HTTPS links using asynchronous I/O and rich terminal UI.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
import os
from pathlib import Path
import re
import sys
from typing import List, Optional, Set, Tuple
import urllib.parse

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt
from rich.table import Table

# Reconfigure stdout/stderr encoding on Windows to prevent UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass

# Initialize rich console instance
console = Console()

# Directories to exclude from recursive file discovery
EXCLUDED_DIRS: Set[str] = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".idea",
    ".vscode",
}

# Standard headers for HTTP requests
HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Concurrency limit for HTTP requests
CONCURRENCY_LIMIT: int = 15
REQUEST_TIMEOUT: float = 5.0


class LinkType(Enum):
    LOCAL = auto()
    HTTP = auto()


class LinkStatus(Enum):
    VALID = auto()
    BROKEN = auto()


@dataclass
class LinkItem:
    source_file: Path
    raw_target: str
    clean_target: str
    link_type: LinkType
    line_number: int


@dataclass
class CheckResult:
    link_item: LinkItem
    status: LinkStatus
    detail: str
    http_code: Optional[int] = None


def discover_md_files(root_path: Path) -> List[Path]:
    """
    Recursively discover all Markdown files matching '.md' extension under root_path,
    excluding any files located within ignored directories (e.g., node_modules, .git, venv).
    """
    md_files: List[Path] = []
    resolved_root = root_path.resolve()

    for item in resolved_root.rglob("*"):
        if item.is_file() and item.suffix.lower() == ".md":
            # Check relative directory components against EXCLUDED_DIRS
            rel_parts = item.relative_to(resolved_root).parts
            if not any(part in EXCLUDED_DIRS for part in rel_parts[:-1]):
                md_files.append(item)

    return sorted(md_files)


class LinkExtractor:
    r"""
    Extracts local file paths and HTTP/HTTPS links from Markdown content.
    
    KEY SECTION COMMENT - LINK EXTRACTION REGEX & CODE BLOCK STRIPPING LOGIC:
    -------------------------------------------------------------------------
    1. Code Block Stripping:
       To prevent false positives from documentation examples, code snippets,
       or shell commands inside markdown files, code blocks are stripped prior to link extraction.
       We match fenced code blocks (``` ... ``` or ~~~ ... ~~~) and inline code spans (` ... `).
       Replacing them with whitespace of equivalent length preserves line counts so that
       extracted link line numbers accurately match the source file lines.

    2. Link Extraction Regex:
       - Standard Markdown Links & Images:
         Pattern: r'(?<!\\)(!?)(?:\[([^\]]*)\])\(([^)\s]+)(?:\s+["\'][^"\']*["\'])?\)'
         Captures `[text](url)` and `![alt](url)`, accounting for optional title quotes.
       - Autolinks:
         Pattern: r'<(https?://[^\s>]+|file://[^\s>]+)>'
         Captures explicit angle-bracket autolinks `<http...>` or `<file...>`.

    3. Target Filtering:
       - In-page anchors (e.g., `#section-heading`) are ignored.
       - Non-HTTP local protocols (`mailto:`, `tel:`, `javascript:`) are ignored.
    """

    # Matches triple backticks, triple tildes, and single inline backticks
    CODE_BLOCK_PATTERN = re.compile(
        r"```[^\n]*\n[\s\S]*?```|~~~[^\n]*\n[\s\S]*?~~~|`[^`\n]+`",
        re.MULTILINE,
    )

    # Standard Markdown link format: [text](url "optional title") or ![alt](url)
    MARKDOWN_LINK_PATTERN = re.compile(
        r"(?<!\\)(!?)(?:\[([^\]]*)\])\(([^)\s]+)(?:\s+[\"\'][^\"\']*[\"\'])?\)"
    )

    # Markdown autolink format: <http://...> or <https://...> or <file://...>
    AUTOLINK_PATTERN = re.compile(
        r"<(https?://[^\s>]+|file://[^\s>]+)>",
        re.IGNORECASE,
    )

    @classmethod
    def extract_links(cls, file_path: Path) -> List[LinkItem]:
        """Reads a Markdown file and extracts all valid target links."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        # Replace code blocks with spaces/newlines to preserve line indexing
        def replace_with_whitespace(match: re.Match) -> str:
            text = match.group(0)
            return "".join("\n" if char == "\n" else " " for char in text)

        stripped_content = cls.CODE_BLOCK_PATTERN.sub(replace_with_whitespace, content)
        lines = stripped_content.splitlines()

        links: List[LinkItem] = []

        for line_idx, line in enumerate(lines, start=1):
            # Extract standard markdown links & images
            for match in cls.MARKDOWN_LINK_PATTERN.finditer(line):
                raw_target = match.group(3).strip()
                link_item = cls._process_target(file_path, raw_target, line_idx)
                if link_item:
                    links.append(link_item)

            # Extract autolinks <http...>
            for match in cls.AUTOLINK_PATTERN.finditer(line):
                raw_target = match.group(1).strip()
                link_item = cls._process_target(file_path, raw_target, line_idx)
                if link_item:
                    links.append(link_item)

        return links

    @classmethod
    def _process_target(
        cls, source_file: Path, raw_target: str, line_number: int
    ) -> Optional[LinkItem]:
        """Filters ignored schemes/anchors and categorizes the link type."""
        # Ignore empty targets or in-page anchors
        if not raw_target or raw_target.startswith("#"):
            return None

        # Ignore non-HTTP local protocols
        lower_target = raw_target.lower()
        if lower_target.startswith(("mailto:", "tel:", "javascript:")):
            return None

        # Categorize HTTP vs Local
        if lower_target.startswith(("http://", "https://")):
            return LinkItem(
                source_file=source_file,
                raw_target=raw_target,
                clean_target=raw_target,
                link_type=LinkType.HTTP,
                line_number=line_number,
            )
        else:
            return LinkItem(
                source_file=source_file,
                raw_target=raw_target,
                clean_target=raw_target,
                link_type=LinkType.LOCAL,
                line_number=line_number,
            )


class LinkChecker:
    """
    Performs link checking for local files and remote HTTP/HTTPS endpoints.
    """

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self.semaphore = semaphore

    @staticmethod
    def check_local_link(link_item: LinkItem) -> CheckResult:
        """
        KEY SECTION COMMENT - RELATIVE PATH RESOLUTION LOGIC:
        -----------------------------------------------------
        1. Contextual Resolution:
           Relative paths (e.g., `./image.png`, `docs/setup.md`, `../styles/main.css`)
           MUST be resolved strictly relative to the directory containing the Markdown file
           (`link_item.source_file.parent / clean_target`), NEVER relative to the CLI invocation root.

        2. Anchor Stripping:
           Files referenced with fragment anchors (e.g., `guide.md#installation`) must have the anchor
           part (`#installation`) removed before checking for file existence on disk.

        3. `file://` Scheme Decoding:
           `file://` URLs are parsed and decoded using `urllib.parse.unquote()`. On Windows systems,
           leading slashes on drive paths (e.g., `/C:/path`) are properly trimmed.

        4. Existence Check:
           Local file existence is validated strictly via `.exists()`.
        """
        raw_target = link_item.clean_target

        # Handle file:// scheme parsing
        if raw_target.lower().startswith("file://"):
            parsed = urllib.parse.urlparse(raw_target)
            decoded_path = urllib.parse.unquote(parsed.path)
            # Remove leading slash on Windows drive paths (e.g. /C:/path -> C:/path)
            if (
                os.name == "nt"
                and decoded_path.startswith("/")
                and len(decoded_path) > 2
                and decoded_path[2] == ":"
            ):
                decoded_path = decoded_path[1:]
            target_path_str = decoded_path
        else:
            target_path_str = raw_target

        # Strip fragment anchors (#section)
        target_no_anchor = target_path_str.split("#")[0]
        if not target_no_anchor:
            # The link was purely an anchor on the current file (e.g. #section)
            return CheckResult(
                link_item=link_item,
                status=LinkStatus.VALID,
                detail="In-page Anchor",
            )

        # Decode URL percent-encoding (e.g. %20 -> space)
        unquoted_target = urllib.parse.unquote(target_no_anchor)
        target_path = Path(unquoted_target)

        # Resolve path
        if target_path.is_absolute():
            resolved_path = target_path.resolve()
        else:
            # Strictly resolve relative to the Markdown file's parent directory
            resolved_path = (link_item.source_file.parent / target_path).resolve()

        if resolved_path.exists():
            return CheckResult(
                link_item=link_item,
                status=LinkStatus.VALID,
                detail="File exists",
            )
        else:
            return CheckResult(
                link_item=link_item,
                status=LinkStatus.BROKEN,
                detail="File not found",
            )

    async def check_http_link(
        self, client: httpx.AsyncClient, link_item: LinkItem
    ) -> CheckResult:
        """
        KEY SECTION COMMENT - ASYNC NETWORK ERROR HANDLING & RETRY STRATEGY:
        --------------------------------------------------------------------
        1. Concurrency Management:
           All network requests are throttled using an `asyncio.Semaphore(15)` to avoid exceeding
           system descriptor limits or triggering remote rate limiters.

        2. Timeout & User-Agent:
           Each request enforces a 5.0-second timeout with a modern browser User-Agent header.

        3. HEAD -> GET Strategy with Rate Limit / Timeout Resilience:
           First, a lightweight `HEAD` request is issued. If the server responds with status code
           `>= 400` (e.g. `403`, `404`, `405`, `500`), we retry with a streaming `GET` request
           (`client.stream("GET", url)`).
           If a server returns `429 Too Many Requests` or encounters a transient `TimeoutException`,
           we apply a brief backoff delay (0.5s - 1.0s) and attempt one retry.

        4. Exception Resilience:
           Catches specific `httpx` exceptions (`TimeoutException`, `ConnectError`, `HTTPError`) and
           general exceptions, mapping them to clear descriptive statuses (e.g., "Timeout",
           "Connection Refused") and treating status codes >= 400 as BROKEN links.
        """
        url = link_item.clean_target

        async with self.semaphore:
            for attempt in range(2):
                try:
                    # Attempt fast HEAD request first
                    response = await client.head(url)

                    # Retry with GET stream if server rejects, fails, or returns >= 400 on HEAD
                    if response.status_code >= 400:
                        async with client.stream("GET", url) as stream_resp:
                            status_code = stream_resp.status_code
                            reason = stream_resp.reason_phrase
                    else:
                        status_code = response.status_code
                        reason = response.reason_phrase

                    # Handle 429 Too Many Requests with brief pause & retry
                    if status_code == 429 and attempt == 0:
                        await asyncio.sleep(1.0)
                        continue

                    if status_code < 400:
                        return CheckResult(
                            link_item=link_item,
                            status=LinkStatus.VALID,
                            detail=f"{status_code} {reason}".strip(),
                            http_code=status_code,
                        )
                    else:
                        return CheckResult(
                            link_item=link_item,
                            status=LinkStatus.BROKEN,
                            detail=f"{status_code} {reason}".strip(),
                            http_code=status_code,
                        )

                except httpx.TimeoutException:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    return CheckResult(
                        link_item=link_item,
                        status=LinkStatus.BROKEN,
                        detail="Timeout (5.0s)",
                    )
                except httpx.ConnectError:
                    return CheckResult(
                        link_item=link_item,
                        status=LinkStatus.BROKEN,
                        detail="Connection Refused",
                    )
                except httpx.HTTPError as exc:
                    return CheckResult(
                        link_item=link_item,
                        status=LinkStatus.BROKEN,
                        detail=f"HTTP Error ({type(exc).__name__})",
                    )
                except Exception as exc:
                    return CheckResult(
                        link_item=link_item,
                        status=LinkStatus.BROKEN,
                        detail=f"Error ({type(exc).__name__})",
                    )

            # Fallback result if loop exits
            return CheckResult(
                link_item=link_item,
                status=LinkStatus.BROKEN,
                detail="Max retries exceeded",
            )


def render_report(
    results: List[CheckResult],
    root_path: Path,
    scanned_file_count: int,
) -> None:
    """Renders the execution results table and summary panel using rich."""
    table = Table(
        title="[bold cyan]Markdown Link Verification Results[/bold cyan]",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )

    table.add_column("Source File", style="dim", ratio=3)
    table.add_column("Link Target", ratio=4)
    table.add_column("Type", justify="center", ratio=1)
    table.add_column("Status", justify="center", ratio=1)
    table.add_column("Details / Code", ratio=3)

    valid_count = 0
    broken_count = 0

    for res in results:
        item = res.link_item
        # Format source file path relative to scanning root for brevity
        try:
            rel_source = item.source_file.relative_to(root_path)
            source_str = f"{rel_source}:{item.line_number}"
        except ValueError:
            source_str = f"{item.source_file.name}:{item.line_number}"

        type_str = "HTTP" if item.link_type == LinkType.HTTP else "Local"

        if res.status == LinkStatus.VALID:
            valid_count += 1
            status_markup = "[bold green]VALID[/bold green]"
            detail_markup = f"[green]{res.detail}[/green]"
        else:
            broken_count += 1
            status_markup = "[bold red]BROKEN[/bold red]"
            detail_markup = f"[red]{res.detail}[/red]"

        table.add_row(
            source_str,
            item.raw_target,
            type_str,
            status_markup,
            detail_markup,
        )

    console.print()
    if results:
        console.print(table)
    else:
        console.print("[yellow]No links were found in the scanned files.[/yellow]")

    console.print()

    # Summary Panel
    summary_content = (
        f"[bold]Total .md Files Scanned:[/] {scanned_file_count}\n"
        f"[bold]Total Links Found:[/] {len(results)}\n"
        f"[bold green]Valid Links Count:[/] {valid_count}\n"
        f"[bold red]Broken Links Count:[/] {broken_count}"
    )

    panel_border_style = "green" if broken_count == 0 else "red"
    summary_panel = Panel(
        summary_content,
        title="[bold]Scan Summary[/bold]",
        subtitle="`md-link-checker` Report",
        border_style=panel_border_style,
        expand=False,
    )
    console.print(summary_panel)


async def main() -> None:
    """Main CLI execution entrypoint."""
    console.print(
        "[bold cyan]Markdown Link & Dead Code Checker (md-link-checker)[/bold cyan]\n"
    )

    # Interactive Path Prompt
    user_input = Prompt.ask(
        "Enter directory path to check (press Enter to scan current directory)",
        default="",
        console=console,
    )

    clean_input = user_input.strip()
    if not clean_input:
        target_dir = Path.cwd().resolve()
    else:
        target_dir = Path(clean_input).resolve()

    # Path Validation
    if not target_dir.exists() or not target_dir.is_dir():
        console.print(
            f"[bold red]Error:[/] Path '{target_dir}' does not exist or is not a valid directory.",
            style="bold red",
        )
        sys.exit(1)

    console.print(f"[dim]Scanning directory:[/] {target_dir}\n")

    # Discover Markdown files
    md_files = discover_md_files(target_dir)

    if not md_files:
        console.print(f"[yellow]No .md files found in '{target_dir}'.[/yellow]")
        sys.exit(0)

    # Extract links from files
    all_links: List[LinkItem] = []
    for md_file in md_files:
        links = LinkExtractor.extract_links(md_file)
        all_links.extend(links)

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    checker = LinkChecker(semaphore)
    results: List[CheckResult] = []

    # Configure async HTTP client
    limits = httpx.Limits(max_keepalive_connections=15, max_connections=20)
    timeout = httpx.Timeout(REQUEST_TIMEOUT)

    # Process links with live Rich progress indicator
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        check_task = progress.add_task(
            "Verifying links...", total=len(all_links) if all_links else 1
        )

        async with httpx.AsyncClient(
            headers=HEADERS, limits=limits, timeout=timeout, follow_redirects=True
        ) as client:
            tasks = []
            for link in all_links:
                if link.link_type == LinkType.LOCAL:
                    # Synchronous local check run in execution loop
                    res = LinkChecker.check_local_link(link)
                    results.append(res)
                    progress.advance(check_task)
                else:
                    # Async network check
                    async def task_wrapper(l_item: LinkItem) -> CheckResult:
                        res_http = await checker.check_http_link(client, l_item)
                        progress.advance(check_task)
                        return res_http

                    tasks.append(task_wrapper(link))

            if tasks:
                http_results = await asyncio.gather(*tasks)
                results.extend(http_results)

    # Sort results by source file and line number
    results.sort(key=lambda r: (r.link_item.source_file, r.link_item.line_number))

    # Render results table & summary
    render_report(results, target_dir, len(md_files))

    # Return exit codes for CI/CD compatibility
    has_broken = any(r.status == LinkStatus.BROKEN for r in results)
    if has_broken:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
