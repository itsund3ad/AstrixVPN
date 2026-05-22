# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Interactive Rich TUI menu for astrix-server
# Features: setup wizard, live stats, session viewer, diagnostics

import asyncio
import json
import logging
import os
import time
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
from rich.text import Text
from rich.syntax import Syntax

from astrix_server import __version__
from astrix_server.config.server import ServerConfig, load_server_config

logger = logging.getLogger("cli")

ASTRIX_BANNER = f"""
╔══════════════════════════════════════════════╗
║              Astrix Server v{__version__:<8}           ║
║     by UNDEAD (github.com/itsund3ad)         ║
║           VPS Exit Server                    ║
╚══════════════════════════════════════════════╝
"""


class ServerShell:
    def __init__(self):
        self.console = Console()
        self.config_path = "server_config.json"
        self.config: Optional[ServerConfig] = None
        self.running = False
        self._app_ref = None
        self._runner_ref = None

    def _clear(self):
        os.system("cls" if os.name == "nt" else "clear")

    def _print_banner(self):
        self.console.print(ASTRIX_BANNER, style="bold cyan")

    def _print_menu(self):
        menu = Table(box=box.ROUNDED, border_style="cyan")
        menu.add_column("Option", style="bold yellow", width=6)
        menu.add_column("Action", style="white")
        menu.add_row("[1]", "Start server")
        menu.add_row("[2]", "Configuration")
        menu.add_row("[3]", "View stats / live dashboard")
        menu.add_row("[4]", "View active sessions")
        menu.add_row("[5]", "Setup wizard (first-time)")
        menu.add_row("[6]", "Import / Export config")
        menu.add_row("[7]", "About")
        menu.add_row("[8]", "Exit")
        self.console.print(menu)

    def _config_menu(self):
        while True:
            self._clear()
            self._print_banner()
            self._show_config_summary()
            self.console.print("")
            menu = Table(box=box.ROUNDED, border_style="yellow")
            menu.add_column("Option", style="bold yellow", width=6)
            menu.add_column("Action", style="white")
            menu.add_row("[1]", "Load / reload config from file")
            menu.add_row("[2]", "Edit server_host / server_port")
            menu.add_row("[3]", "Set tunnel_key (generate new)")
            menu.add_row("[4]", "Toggle upstream_proxy")
            menu.add_row("[5]", "Toggle debug_timing")
            menu.add_row("[6]", "Save config to file")
            menu.add_row("[b]", "Back to main")
            self.console.print(menu)

            choice = Prompt.ask("Choice", default="b").strip().lower()
            if choice == "1":
                self._load_config()
            elif choice == "2":
                self._edit_listen()
            elif choice == "3":
                self._set_tunnel_key()
            elif choice == "4":
                self._toggle_proxy()
            elif choice == "5":
                if self.config:
                    self.config.debug_timing = not self.config.debug_timing
                    self.console.print(f"[green]debug_timing: {self.config.debug_timing}[/green]")
                _ = Prompt.ask("Press Enter to continue")
            elif choice == "6":
                self._save_config()
            elif choice in ("b", "back"):
                return

    def _show_config_summary(self):
        if not self.config:
            self.console.print("[yellow]No config loaded.[/yellow]")
        else:
            cfg = self.config
            t = Table(box=box.ROUNDED, border_style="green")
            t.add_column("Field", style="bold white")
            t.add_column("Value", style="cyan")
            t.add_row("server_host", cfg.server_host)
            t.add_row("server_port", str(cfg.server_port))
            t.add_row("tunnel_key", f"{cfg.tunnel_key[:8]}...{cfg.tunnel_key[-8:]}" if cfg.tunnel_key else "(not set)")
            t.add_row("upstream_proxy", cfg.upstream_proxy or "(none)")
            t.add_row("debug_timing", str(cfg.debug_timing))
            self.console.print(t)

    def _load_config(self):
        path = Prompt.ask("Config path", default=self.config_path)
        try:
            self.config = load_server_config(path)
            self.config_path = path
            self.console.print(f"[green]Config loaded from {path}[/green]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
        _ = Prompt.ask("Press Enter to continue")

    def _edit_listen(self):
        if not self.config:
            return
        self.config.server_host = Prompt.ask("Server host", default=self.config.server_host)
        self.config.server_port = int(Prompt.ask("Server port", default=str(self.config.server_port)))
        self.console.print("[green]Updated.[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _set_tunnel_key(self):
        if not self.config:
            return
        if Confirm.ask("Generate new random tunnel_key?", default=True):
            import secrets
            self.config.tunnel_key = secrets.token_hex(32)
            self.console.print(f"[green]Generated: {self.config.tunnel_key[:16]}...{self.config.tunnel_key[-16:]}[/green]")
        else:
            key = Prompt.ask("tunnel_key (64 hex chars)", password=True)
            if len(key) == 64:
                try:
                    bytes.fromhex(key)
                    self.config.tunnel_key = key
                    self.console.print("[green]tunnel_key set.[/green]")
                except ValueError:
                    self.console.print("[red]Contains non-hex characters[/red]")
            else:
                self.console.print(f"[red]Must be 64 chars, got {len(key)}[/red]")
        _ = Prompt.ask("Press Enter to continue")

    def _toggle_proxy(self):
        if not self.config:
            return
        current = self.config.upstream_proxy
        if current:
            self.config.upstream_proxy = ""
            self.console.print("[green]upstream_proxy disabled[/green]")
        else:
            proxy = Prompt.ask("SOCKS5 proxy URL (e.g. socks5://127.0.0.1:40000)")
            self.config.upstream_proxy = proxy.strip()
            self.console.print(f"[green]Proxy set to {proxy}[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _save_config(self):
        if not self.config:
            self.console.print("[red]No config loaded[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        path = Prompt.ask("Save to", default=self.config_path)
        data = {
            "server_host": self.config.server_host,
            "server_port": self.config.server_port,
            "tunnel_key": self.config.tunnel_key,
            "upstream_proxy": self.config.upstream_proxy,
            "debug_timing": self.config.debug_timing,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.config_path = path
        self.console.print(f"[green]Saved to {path}[/green]")
        _ = Prompt.ask("Press Enter to continue")

    async def _start_server(self):
        if not self.config:
            self.console.print("[red]Load a config first[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        if self.running:
            self.console.print("[yellow]Already running[/yellow]")
            return

        from astrix_server.exit.server import _make_app, apply_socket_opts
        from aiohttp import web

        self._clear()
        self.console.print(Panel("[bold green]Starting Astrix server...[/bold green]"))

        app = _make_app(self.config)

        self._app_ref = app
        runner = web.AppRunner(app)
        self._runner_ref = runner

        await runner.setup()
        site = web.TCPSite(
            runner,
            self.config.server_host,
            self.config.server_port,
        )
        await site.start()

        apply_socket_opts(runner)

        self.running = True

        self.console.print(f"[green]Listening on {self.config.server_host}:{self.config.server_port}[/green]")
        self.console.print(f"[dim]Health check: http://{self.config.server_host}:{self.config.server_port}/healthz[/dim]")
        self.console.print("[dim]Press Ctrl+C to stop[/dim]")

        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
        finally:
            await runner.cleanup()
            self.running = False
            self._app_ref = None
            self._runner_ref = None

    async def _show_stats(self):
        self._clear()
        self._print_banner()
        if not self._app_ref:
            self.console.print("[yellow]Server not running[/yellow]")
            _ = Prompt.ask("Press Enter to continue")
            return

        stats = self._app_ref.get("stats")
        pool = self._app_ref.get("pool")
        if stats and pool:
            t = Table(box=box.ROUNDED, border_style="cyan")
            t.add_column("Metric", style="bold white")
            t.add_column("Value", style="green")
            t.add_row("Active sessions", str(pool.active_count()))
            t.add_row("Tx buffered", f"{pool.tx_buffered_bytes() / 1024:.1f} KB")
            upt = time.monotonic() - stats.start_time
            hours, remainder = divmod(int(upt), 3600)
            minutes, seconds = divmod(remainder, 60)
            t.add_row("Uptime", f"{hours}h {minutes}m {seconds}s")
            t.add_row("Polls served", str(stats.polls_served))
            t.add_row("Bytes received", f"{stats.bytes_in / 1024 / 1024:.2f} MB")
            t.add_row("Bytes sent", f"{stats.bytes_out / 1024 / 1024:.2f} MB")
            t.add_row("Active upstream conns", str(stats.active_conns))
            self.console.print(t)
        else:
            self.console.print("[yellow]Stats not available[/yellow]")
        _ = Prompt.ask("Press Enter to continue")

    async def _show_sessions(self):
        self._clear()
        self._print_banner()
        if not self._app_ref:
            self.console.print("[yellow]Server not running[/yellow]")
            _ = Prompt.ask("Press Enter to continue")
            return

        pool = self._app_ref.get("pool")
        if pool:
            snapshots = pool.snapshot_all()
            if not snapshots:
                self.console.print("[yellow]No active sessions[/yellow]")
            else:
                t = Table(box=box.SIMPLE)
                t.add_column("ID", style="cyan", width=16)
                t.add_column("Target", style="white")
                t.add_column("Seq", style="yellow", width=6)
                t.add_column("Tx Q", style="green", width=5)
                t.add_column("Rx Q", style="green", width=5)
                t.add_column("Tx Buf", style="yellow", width=8)
                t.add_column("Last Act.", style="white", width=10)
                now_t = time.time()
                for s in snapshots:
                    last = now_t - s.last_activity
                    last_str = f"{last:.0f}s ago" if last < 3600 else f"{last/3600:.1f}h ago"
                    t.add_row(
                        s.session_id.hex()[:12],
                        s.target[:30],
                        str(s.seq),
                        str(len(s.tx_q)),
                        str(len(s.rx_q)),
                        f"{s.tx_bytes / 1024:.1f}K",
                        last_str,
                    )
                self.console.print(t)
                self.console.print(f"\n[dim]Total: {len(snapshots)} sessions[/dim]")
        else:
            self.console.print("[yellow]Session pool not available[/yellow]")
        _ = Prompt.ask("Press Enter to continue")

    def _setup_wizard(self):
        self._clear()
        self._print_banner()
        self.console.print("[bold green]=== First-time Setup Wizard ===[/bold green]\n")
        self.console.print("I'll help you create a server_config.json step by step.\n")

        cfg = ServerConfig()
        self.config = cfg

        # 1. Listen address
        self.console.print("[bold]Step 1: Server Listen Address[/bold]")
        cfg.server_host = Prompt.ask("  Server host", default="0.0.0.0")
        cfg.server_port = int(Prompt.ask("  Server port", default="8443"))

        # 2. Tunnel key
        self.console.print("\n[bold]Step 2: Tunnel Key[/bold]")
        self.console.print("  Must be the same 64-char hex key as the client.")
        if Confirm.ask("  Generate new key?", default=True):
            import secrets
            cfg.tunnel_key = secrets.token_hex(32)
            self.console.print(f"  [green]Key: {cfg.tunnel_key}[/green]")
            self.console.print("  [red]IMPORTANT: Save this key! You'll need it in client_config.json too.[/red]")
        else:
            key = Prompt.ask("  Enter 64-char hex key", password=True)
            cfg.tunnel_key = key

        # 3. Proxy (optional)
        self.console.print("\n[bold]Step 3: Upstream Proxy (optional)[/bold]")
        self.console.print("  Only needed if your VPS routes traffic through a SOCKS5 proxy.")
        if Confirm.ask("  Use upstream proxy?", default=False):
            proxy = Prompt.ask("  SOCKS5 URL (e.g. socks5://127.0.0.1:40000)")
            cfg.upstream_proxy = proxy.strip()

        # Save
        path = Prompt.ask("\nSave config to", default=self.config_path)
        self.config_path = path
        data = {
            "server_host": cfg.server_host,
            "server_port": cfg.server_port,
            "tunnel_key": cfg.tunnel_key,
            "upstream_proxy": cfg.upstream_proxy or "",
            "debug_timing": cfg.debug_timing,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        self.console.print(f"\n[bold green]Config saved to {path}![/bold green]")
        self.console.print("[green]You can now start the server (option 1).[/green]")
        self.console.print("[yellow]Make sure to update client_config.json with the same tunnel_key![/yellow]")
        _ = Prompt.ask("Press Enter to continue")

    def _import_export_menu(self):
        while True:
            self._clear()
            self._print_banner()
            menu = Table(box=box.ROUNDED, border_style="yellow")
            menu.add_column("Option", style="bold yellow", width=6)
            menu.add_column("Action", style="white")
            menu.add_row("[1]", "Import config from file")
            menu.add_row("[2]", "Export config to file")
            menu.add_row("[3]", "View config JSON (raw)")
            menu.add_row("[b]", "Back")
            self.console.print(menu)

            choice = Prompt.ask("Choice", default="b").strip().lower()
            if choice == "1":
                self._import_config()
            elif choice == "2":
                self._export_config()
            elif choice == "3":
                self._view_raw_config()
            elif choice in ("b", "back"):
                return

    def _import_config(self):
        path = Prompt.ask("Path to config file", default=self.config_path)
        if not os.path.exists(path):
            self.console.print(f"[red]File not found: {path}[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        try:
            self.config = load_server_config(path)
            self.config_path = path
            self.console.print(f"[green]Imported from {path}[/green]")
        except Exception as e:
            self.console.print(f"[red]Import failed: {e}[/red]")
        _ = Prompt.ask("Press Enter to continue")

    def _export_config(self):
        if not self.config:
            self.console.print("[red]No config to export[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        path = Prompt.ask("Export to path", default="astrix_server_export.json")
        data = {
            "server_host": self.config.server_host,
            "server_port": self.config.server_port,
            "tunnel_key": self.config.tunnel_key,
            "upstream_proxy": self.config.upstream_proxy or "",
            "debug_timing": self.config.debug_timing,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.console.print(f"[green]Exported to {path}[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _view_raw_config(self):
        if not self.config:
            self.console.print("[red]No config loaded[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        self._clear()
        self.console.print("[bold]Raw Config JSON:[/bold]\n")
        data = {
            "server_host": self.config.server_host,
            "server_port": self.config.server_port,
            "tunnel_key": self.config.tunnel_key[:8] + "..." + self.config.tunnel_key[-8:],
            "upstream_proxy": self.config.upstream_proxy or "",
            "debug_timing": self.config.debug_timing,
        }
        self.console.print(Syntax(json.dumps(data, indent=2), "json"))
        _ = Prompt.ask("Press Enter to continue")

    def _show_about(self):
        self._clear()
        text = Text()
        text.append(f"Astrix Server v{__version__}\n", style="bold cyan")
        text.append("\nTCP-over-Apps-Script censorship circumvention exit server.\n")
        text.append("\nAuthor: ", style="bold")
        text.append("UNDEAD\n")
        text.append("GitHub: ", style="bold")
        text.append("https://github.com/itsund3ad\n")
        text.append("\nOptimized Python port of GooseRelayVPN.\n")
        text.append("Features: AES-256-GCM + Zstd, long-poll,\n")
        text.append("DNS caching, async upstream, Rich TUI.\n")
        self.console.print(Panel(text, border_style="cyan"))
        _ = Prompt.ask("Press Enter to continue")

    async def run(self):
        self._clear()
        self._print_banner()

        if os.path.exists(self.config_path):
            try:
                self.config = load_server_config(self.config_path)
                self.console.print(f"[dim]Auto-loaded config from {self.config_path}[/dim]")
            except Exception as e:
                self.console.print(f"[dim]Auto-load: {e}[/dim]")

        while True:
            self.console.print("")
            self._print_menu()
            choice = Prompt.ask("Choice", default="1").strip().lower()

            if choice == "1":
                await self._start_server()
                self._clear()
                self._print_banner()
            elif choice == "2":
                self._config_menu()
                self._clear()
                self._print_banner()
            elif choice == "3":
                await self._show_stats()
                self._clear()
                self._print_banner()
            elif choice == "4":
                await self._show_sessions()
                self._clear()
                self._print_banner()
            elif choice == "5":
                self._setup_wizard()
                self._clear()
                self._print_banner()
            elif choice == "6":
                self._import_export_menu()
                self._clear()
                self._print_banner()
            elif choice == "7":
                self._show_about()
                self._clear()
                self._print_banner()
            elif choice == "8":
                if self.running:
                    self.console.print("[yellow]Stopping server...[/yellow]")
                self.console.print("[cyan]Goodbye![/cyan]")
                return
