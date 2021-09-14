import json
import sys
import typing

import click
import pygments.lexers
import pygments.util
import rich.console
import rich.progress
import rich.syntax

from ._client import Client
from ._exceptions import RequestError
from ._models import Request, Response


def print_help() -> None:
    console = rich.console.Console()

    console.print("[bold]HTTPX :butterfly:", justify="center")
    console.print()
    console.print("A next generation HTTP client.", justify="center")
    console.print()
    console.print(
        "Usage: [bold]httpx[/bold] [cyan]<URL> [OPTIONS][/cyan] ", justify="left"
    )
    console.print()

    table = rich.table.Table.grid(padding=1, pad_edge=True)
    table.add_column("Parameter", no_wrap=True, justify="left", style="bold")
    table.add_column("Description")
    table.add_row(
        "-m, --method [cyan]METHOD",
        "Request method, such as GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD.\n"
        "[Default: GET, or POST if a request body is included]",
    )
    table.add_row(
        "-p, --params [cyan]<NAME VALUE> ...",
        "Query parameters to include in the request URL.",
    )
    table.add_row(
        "-c, --content [cyan]TEXT", "Byte content to include in the request body."
    )
    table.add_row(
        "-d, --data [cyan]<NAME VALUE> ...", "Form data to include in the request body."
    )
    table.add_row(
        "-f, --files [cyan]<NAME FILENAME> ...",
        "Form files to include in the request body.",
    )
    table.add_row("-j, --json [cyan]TEXT", "JSON data to include in the request body.")
    table.add_row(
        "-h, --headers [cyan]<NAME VALUE> ...",
        "Include additional HTTP headers in the request.",
    )
    table.add_row(
        "--cookies [cyan]<NAME VALUE> ...", "Cookies to include in the request."
    )
    table.add_row(
        "--auth [cyan]<USER PASS>",
        "Username and password to include in the request. Specify '-' for the password to use "
        "a password prompt. Note that using --verbose/-v will expose the Authorization "
        "header, including the password encoding in a trivially reversible format.",
    )

    table.add_row(
        "--proxy [cyan]URL",
        "Send the request via a proxy. Should be the URL giving the proxy address.",
    )

    table.add_row(
        "--timeout [cyan]FLOAT",
        "Timeout value to use for network operations, such as establishing the connection, "
        "reading some data, etc... [Default: 5.0]",
    )

    table.add_row("--follow-redirects", "Automatically follow redirects.")
    table.add_row("--no-verify", "Disable SSL verification.")
    table.add_row(
        "--http2", "Send the request using HTTP/2, if the remote server supports it."
    )

    table.add_row(
        "--download [cyan]FILE",
        "Save the response content as a file, rather than displaying it.",
    )

    table.add_row("-v, --verbose", "Verbose output. Show request as well as response.")
    table.add_row("--help", "Show this message and exit.")
    console.print(table)


def get_lexer_for_response(response: Response) -> str:
    content_type = response.headers.get("Content-Type")
    if content_type is not None:
        mime_type, _, _ = content_type.partition(";")
        try:
            return pygments.lexers.get_lexer_for_mimetype(mime_type.strip()).name
        except pygments.util.ClassNotFound:  # pragma: nocover
            pass
    return ""  # pragma: nocover


def format_request_headers(request: Request) -> str:
    target = request.url.raw[-1].decode("ascii")
    lines = [f"{request.method} {target} HTTP/1.1"] + [
        f"{name.decode('ascii')}: {value.decode('ascii')}"
        for name, value in request.headers.raw
    ]
    return "\n".join(lines)


def format_response_headers(response: Response) -> str:
    lines = [
        f"{response.http_version} {response.status_code} {response.reason_phrase}"
    ] + [
        f"{name.decode('ascii')}: {value.decode('ascii')}"
        for name, value in response.headers.raw
    ]
    return "\n".join(lines)


def print_request_headers(request: Request) -> None:
    console = rich.console.Console()
    http_text = format_request_headers(request)
    syntax = rich.syntax.Syntax(http_text, "http", theme="ansi_dark", word_wrap=True)
    console.print(syntax)
    syntax = rich.syntax.Syntax("", "http", theme="ansi_dark", word_wrap=True)
    console.print(syntax)


def print_response_headers(response: Response) -> None:
    console = rich.console.Console()
    http_text = format_response_headers(response)
    syntax = rich.syntax.Syntax(http_text, "http", theme="ansi_dark", word_wrap=True)
    console.print(syntax)


def print_delimiter() -> None:
    console = rich.console.Console()
    syntax = rich.syntax.Syntax("", "http", theme="ansi_dark", word_wrap=True)
    console.print(syntax)


def print_redirects(response: Response) -> None:
    if response.has_redirect_location:
        response.read()
        print_response_headers(response)
        print_response(response)


def print_response(response: Response) -> None:
    console = rich.console.Console()
    lexer_name = get_lexer_for_response(response)
    if lexer_name:
        if lexer_name.lower() == "json":
            try:
                data = response.json()
                text = json.dumps(data, indent=4)
            except ValueError:  # pragma: nocover
                text = response.text
        else:
            text = response.text
        syntax = rich.syntax.Syntax(text, lexer_name, theme="ansi_dark", word_wrap=True)
        console.print(syntax)
    else:  # pragma: nocover
        console.print(response.text)


def download_response(response: Response, download: typing.BinaryIO) -> None:
    console = rich.console.Console()
    syntax = rich.syntax.Syntax("", "http", theme="ansi_dark", word_wrap=True)
    console.print(syntax)

    content_length = response.headers.get("Content-Length")
    kwargs = {"total": int(content_length)} if content_length else {}
    with rich.progress.Progress(
        "[progress.description]{task.description}",
        "[progress.percentage]{task.percentage:>3.0f}%",
        rich.progress.BarColumn(bar_width=None),
        rich.progress.DownloadColumn(),
        rich.progress.TransferSpeedColumn(),
    ) as progress:
        description = f"Downloading [bold]{download.name}"
        download_task = progress.add_task(description, **kwargs)  # type: ignore
        for chunk in response.iter_bytes():
            download.write(chunk)
            progress.update(download_task, completed=response.num_bytes_downloaded)


def validate_json(
    ctx: click.Context,
    param: typing.Union[click.Option, click.Parameter],
    value: typing.Any,
) -> typing.Any:
    if value is None:
        return None

    try:
        return json.loads(value)
    except json.JSONDecodeError:  # pragma: nocover
        raise click.BadParameter("Not valid JSON")


def validate_auth(
    ctx: click.Context,
    param: typing.Union[click.Option, click.Parameter],
    value: typing.Any,
) -> typing.Any:
    if value == (None, None):
        return None

    username, password = value
    if password == "-":  # pragma: nocover
        password = click.prompt("Password", hide_input=True)
    return (username, password)


def handle_help(
    ctx: click.Context,
    param: typing.Union[click.Option, click.Parameter],
    value: typing.Any,
) -> None:
    if not value or ctx.resilient_parsing:
        return

    print_help()
    ctx.exit()


@click.command(add_help_option=False)
@click.argument("url", type=str)
@click.option(
    "--method",
    "-m",
    "method",
    type=str,
    help=(
        "Request method, such as GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD. "
        "[Default: GET, or POST if a request body is included]"
    ),
)
@click.option(
    "--params",
    "-p",
    "params",
    type=(str, str),
    multiple=True,
    help="Query parameters to include in the request URL.",
)
@click.option(
    "--content",
    "-c",
    "content",
    type=str,
    help="Byte content to include in the request body.",
)
@click.option(
    "--data",
    "-d",
    "data",
    type=(str, str),
    multiple=True,
    help="Form data to include in the request body.",
)
@click.option(
    "--files",
    "-f",
    "files",
    type=(str, click.File(mode="rb")),
    multiple=True,
    help="Form files to include in the request body.",
)
@click.option(
    "--json",
    "-j",
    "json",
    type=str,
    callback=validate_json,
    help="JSON data to include in the request body.",
)
@click.option(
    "--headers",
    "-h",
    "headers",
    type=(str, str),
    multiple=True,
    help="Include additional HTTP headers in the request.",
)
@click.option(
    "--cookies",
    "cookies",
    type=(str, str),
    multiple=True,
    help="Cookies to include in the request.",
)
@click.option(
    "--auth",
    "auth",
    type=(str, str),
    default=(None, None),
    callback=validate_auth,
    help=(
        "Username and password to include in the request. "
        "Specify '-' for the password to use a password prompt. "
        "Note that using --verbose/-v will expose the Authorization header, "
        "including the password encoding in a trivially reversible format."
    ),
)
@click.option(
    "--proxies",
    "proxies",
    type=str,
    default=None,
    help="Send the request via a proxy. Should be the URL giving the proxy address.",
)
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=5.0,
    help=(
        "Timeout value to use for network operations, such as establishing the "
        "connection, reading some data, etc... [Default: 5.0]"
    ),
)
@click.option(
    "--follow-redirects",
    "follow_redirects",
    is_flag=True,
    default=False,
    help="Automatically follow redirects.",
)
@click.option(
    "--no-verify",
    "verify",
    is_flag=True,
    default=True,
    help="Disable SSL verification.",
)
@click.option(
    "--http2",
    "http2",
    type=bool,
    is_flag=True,
    default=False,
    help="Send the request using HTTP/2, if the remote server supports it.",
)
@click.option(
    "--download",
    type=click.File("wb"),
    help="Save the response content as a file, rather than displaying it.",
)
@click.option(
    "--verbose",
    "-v",
    type=bool,
    is_flag=True,
    default=False,
    help="Verbose. Show request as well as response.",
)
@click.option(
    "--help",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=handle_help,
    help="Show this message and exit.",
)
def main(
    url: str,
    method: str,
    params: typing.List[typing.Tuple[str, str]],
    content: str,
    data: typing.List[typing.Tuple[str, str]],
    files: typing.List[typing.Tuple[str, click.File]],
    json: str,
    headers: typing.List[typing.Tuple[str, str]],
    cookies: typing.List[typing.Tuple[str, str]],
    auth: typing.Optional[typing.Tuple[str, str]],
    proxies: str,
    timeout: float,
    follow_redirects: bool,
    verify: bool,
    http2: bool,
    download: typing.Optional[typing.BinaryIO],
    verbose: bool,
) -> None:
    """
    An HTTP command line client.
    Sends a request and displays the response.
    """
    if not method:
        method = "POST" if content or data or files or json else "GET"

    event_hooks: typing.Dict[str, typing.List[typing.Callable]] = {}
    if verbose:
        event_hooks["request"] = [print_request_headers]
    if follow_redirects:
        event_hooks["response"] = [print_redirects]

    try:
        with Client(
            proxies=proxies,
            timeout=timeout,
            verify=verify,
            http2=http2,
            event_hooks=event_hooks,
        ) as client:
            with client.stream(
                method,
                url,
                params=list(params),
                content=content,
                data=dict(data),
                files=files,  # type: ignore
                json=json,
                headers=headers,
                cookies=dict(cookies),
                auth=auth,
                follow_redirects=follow_redirects,
            ) as response:
                print_response_headers(response)

                if download is not None:
                    download_response(response, download)
                else:
                    response.read()
                    if response.content:
                        print_delimiter()
                        print_response(response)

    except RequestError as exc:
        console = rich.console.Console()
        console.print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)

    sys.exit(0 if response.is_success else 1)