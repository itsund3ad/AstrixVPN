# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Interactive Rich TUI menu for astrix-client
# Features: setup wizard, diagnostics, bandwidth stats, import/export

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout as PTLayout
from prompt_toolkit.layout.containers import Window, HSplit, VSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.application import get_app

from astrix_client import __version__
from astrix_client.config.client import (
    ClientConfig,
    load_client_config,
    ScriptKeyEntry,
)

logger = logging.getLogger("cli")

ASTRIX_BANNER = f"""
╔══════════════════════════════════════════════╗
║              Astrix Client v{__version__:<8}           ║
║     by UNDEAD (github.com/itsund3ad)         ║
║   TCP-over-Apps-Script Censorship Circ.     ║
╚══════════════════════════════════════════════╝
"""


class ClientShell:
    def __init__(self):
        self.console = Console()
        self.config_path = "client_config.json"
        self.config: Optional[ClientConfig] = None
        self.running = False
        self._running_task: Optional[asyncio.Task] = None
        self._carrier_ref = None
        self._socks_ref = None
        self._pool_ref: Optional[SessionPool] = None
        self._bandwidth_last = (0.0, 0)  # (time, bytes_sent)

    def _clear(self):
        os.system("cls" if os.name == "nt" else "clear")

    def _print_banner(self):
        self.console.print(ASTRIX_BANNER, style="bold cyan")

    def _print_menu(self):
        menu = Table(box=box.ROUNDED, border_style="cyan")
        menu.add_column("Option", style="bold yellow", width=6)
        menu.add_column("Action", style="white")
        menu.add_row("[1]", "Start tunnel")
        menu.add_row("[2]", "Configuration")
        menu.add_row("[3]", "View status / live stats")
        menu.add_row("[4]", "Diagnostics (pre-flight check)")
        menu.add_row("[5]", "Setup wizard (first-time)")
        menu.add_row("[6]", "Import / Export config")
        menu.add_row("[7]", "About")
        menu.add_row("[8]", "Exit")
        self.console.print(menu)

    def _config_menu(self):
        while True:
            self._clear()
            self._print_banner()

            if not self.config:
                self.console.print("[yellow]No config loaded.[/yellow]")
            else:
                cfg = self.config
                t = Table(box=box.ROUNDED, border_style="green")
                t.add_column("Field", style="bold white")
                t.add_column("Value", style="cyan")
                t.add_row("socks_host", cfg.socks_host)
                t.add_row("socks_port", str(cfg.socks_port))
                t.add_row("socks_auth", "yes" if cfg.socks_user else "no")
                t.add_row("google_host (edge IP)", cfg.google_host)
                t.add_row("SNI hosts", ", ".join(cfg.sni_hosts))
                t.add_row("Script keys", str(len(cfg.script_keys)))
                t.add_row("tunnel_key", f"{cfg.tunnel_key[:8]}...{cfg.tunnel_key[-8:]}" if cfg.tunnel_key else "(not set)")
                t.add_row("debug_timing", str(cfg.debug_timing))
                t.add_row("coalesce_step_ms", str(cfg.coalesce_step_ms))
                t.add_row("idle_slots_per_bucket", str(cfg.idle_slots_per_bucket))
                self.console.print(t)

            self.console.print("")
            menu = Table(box=box.ROUNDED, border_style="yellow")
            menu.add_column("Option", style="bold yellow", width=6)
            menu.add_column("Action", style="white")
            menu.add_row("[1]", "Load / reload config from file")
            menu.add_row("[2]", "Edit socks_host / socks_port / auth")
            menu.add_row("[3]", "Edit google_host / SNI hosts")
            menu.add_row("[4]", "Add / remove script key")
            menu.add_row("[5]", "Set tunnel_key (generate new)")
            menu.add_row("[6]", "Toggle debug_timing")
            menu.add_row("[7]", "Set coalesce_step_ms")
            menu.add_row("[8]", "Set idle_slots_per_bucket")
            menu.add_row("[9]", "Save config to file")
            menu.add_row("[b]", "Back to main")
            self.console.print(menu)

            choice = Prompt.ask("Choice", default="b").strip().lower()
            if choice == "1":
                self._load_config()
            elif choice == "2":
                self._edit_socks()
            elif choice == "3":
                self._edit_google()
            elif choice == "4":
                self._manage_script_keys()
            elif choice == "5":
                self._set_tunnel_key()
            elif choice == "6":
                if self.config:
                    self.config.debug_timing = not self.config.debug_timing
                    self.console.print(f"[green]debug_timing: {self.config.debug_timing}[/green]")
                _ = Prompt.ask("Press Enter to continue")
            elif choice == "7":
                self._set_coalesce()
            elif choice == "8":
                self._set_idle_slots()
            elif choice == "9":
                self._save_config()
            elif choice in ("b", "back"):
                return

    def _load_config(self):
        path = Prompt.ask("Config path", default=self.config_path)
        try:
            self.config = load_client_config(path)
            self.config_path = path
            self.console.print(f"[green]Config loaded from {path}[/green]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
        _ = Prompt.ask("Press Enter to continue")

    def _edit_socks(self):
        if not self.config:
            return
        self.config.socks_host = Prompt.ask("SOCKS5 host", default=self.config.socks_host)
        self.config.socks_port = int(Prompt.ask("SOCKS5 port", default=str(self.config.socks_port)))
        if Confirm.ask("Enable username/password auth?", default=bool(self.config.socks_user)):
            self.config.socks_user = Prompt.ask("Username", default=self.config.socks_user)
            self.config.socks_pass = Prompt.ask("Password", password=True, default=self.config.socks_pass)
        else:
            self.config.socks_user = ""
            self.config.socks_pass = ""
        self.console.print("[green]Updated.[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _edit_google(self):
        if not self.config:
            return
        self.config.google_host = Prompt.ask("Google edge IP", default=self.config.google_host)
        sni_str = Prompt.ask("SNI hosts (comma-separated)", default=",".join(self.config.sni_hosts))
        self.config.sni_hosts = [h.strip() for h in sni_str.split(",") if h.strip()]
        self.console.print("[green]Updated.[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _manage_script_keys(self):
        if not self.config:
            return
        while True:
            self._clear()
            self.console.print("[bold]Script Keys:[/bold]")
            t = Table(box=box.SIMPLE)
            t.add_column("#", style="yellow", width=3)
            t.add_column("ID", style="cyan")
            t.add_column("Account", style="green")
            for i, entry in enumerate(self.config.script_keys):
                eid = entry.id
                if len(eid) > 50:
                    eid = eid[:24] + "..." + eid[-24:]
                t.add_row(str(i + 1), eid, entry.account or "-")
            self.console.print(t)
            self.console.print("")
            menu = Table(box=box.ROUNDED, border_style="yellow")
            menu.add_column("Option", style="bold yellow", width=6)
            menu.add_column("Action", style="white")
            menu.add_row("[a]", "Add new script key")
            menu.add_row("[r]", "Remove script key")
            menu.add_row("[b]", "Back")
            self.console.print(menu)

            choice = Prompt.ask("Choice", default="b").strip().lower()
            if choice == "a":
                did = Prompt.ask("Deployment ID")
                acct = Prompt.ask("Account label (optional)", default="")
                self.config.script_keys.append(ScriptKeyEntry(id=did.strip(), account=acct.strip()))
                self.console.print("[green]Added.[/green]")
            elif choice == "r":
                idx = int(Prompt.ask("Number to remove")) - 1
                if 0 <= idx < len(self.config.script_keys):
                    removed = self.config.script_keys.pop(idx)
                    self.console.print(f"[green]Removed: {removed.id[:12]}...[/green]")
                else:
                    self.console.print("[red]Invalid index[/red]")
            elif choice in ("b", "back"):
                return
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

    def _set_coalesce(self):
        if not self.config:
            return
        val = Prompt.ask("coalesce_step_ms (0=off)", default=str(self.config.coalesce_step_ms))
        self.config.coalesce_step_ms = max(0, int(val))
        self.console.print("[green]Updated.[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _set_idle_slots(self):
        if not self.config:
            return
        val = Prompt.ask("idle_slots_per_bucket (0-3)", default=str(self.config.idle_slots_per_bucket))
        self.config.idle_slots_per_bucket = max(0, min(3, int(val)))
        self.console.print("[green]Updated.[/green]")
        _ = Prompt.ask("Press Enter to continue")

    def _save_config(self):
        if not self.config:
            self.console.print("[red]No config loaded[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        path = Prompt.ask("Save to", default=self.config_path)
        data = {
            "debug_timing": self.config.debug_timing,
            "socks_host": self.config.socks_host,
            "socks_port": self.config.socks_port,
            "socks_user": self.config.socks_user,
            "socks_pass": self.config.socks_pass,
            "google_host": self.config.google_host,
            "sni": self.config.sni_hosts,
            "script_keys": [
                {"id": e.id, "account": e.account} if e.account else e.id
                for e in self.config.script_keys
            ],
            "tunnel_key": self.config.tunnel_key,
            "coalesce_step_ms": self.config.coalesce_step_ms,
            "idle_slots_per_bucket": self.config.idle_slots_per_bucket,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.config_path = path
        self.console.print(f"[green]Saved to {path}[/green]")
        _ = Prompt.ask("Press Enter to continue")

    async def _start_tunnel(self):
        if not self.config:
            self.console.print("[red]Load a config first (option 2)[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return
        if self.running:
            self.console.print("[yellow]Already running[/yellow]")
            return

        from astrix_client.carrier.client import Carrier
        from astrix_client.socks.server import SOCKS5Server
        from astrix_client.socks.conn import VirtualConn
        from astrix_client.session.session import SessionPool

        self._clear()
        self.console.print(Panel("[bold green]Starting Astrix tunnel...[/bold green]"))

        pool = SessionPool()

        async def handle_new_conn(vconn: VirtualConn):
            """Called when SOCKS5 server accepts a new connection."""
            try:
                sid = await vconn.open()
                logger.info("New session: %s -> %s", sid.hex()[:12], vconn.target)
            except Exception as e:
                logger.error("Failed to open session: %s", e)

        carrier = Carrier(
            config=self.config,
            session_pool=pool,
            conn_callback=handle_new_conn,
        )

        socks_server = SOCKS5Server(
            pool=pool,
            host=self.config.socks_host,
            port=self.config.socks_port,
            auth_required=bool(self.config.socks_user),
            users={self.config.socks_user: self.config.socks_pass} if self.config.socks_user else None,
            conn_callback=handle_new_conn,
        )

        self.running = True
        self._carrier_ref = carrier
        self._socks_ref = socks_server
        self._pool_ref = pool

        self.console.print(f"[green]SOCKS5 proxy ready on {self.config.socks_host}:{self.config.socks_port}[/green]")
        self.console.print("[dim]Press Ctrl+C to stop[/dim]")

        async def run_all():
            async with asyncio.TaskGroup() as tg:
                tg.create_task(carrier.start(), name="carrier")
                tg.create_task(socks_server.start(), name="socks")

        try:
            await run_all()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.console.print(f"[red]Runtime error: {e}[/red]")
        finally:
            await carrier.stop()
            await socks_server.stop()
            self.running = False
            self._carrier_ref = None
            self._socks_ref = None
            self._pool_ref = None

    async def _show_status(self):
        self._clear()
        self._print_banner()
        if self.running:
            self.console.print("[bold green]Tunnel is RUNNING[/bold green]")
            if self._carrier_ref:
                diag = self._carrier_ref.get_diagnostics()
                uptime = diag.get("uptime", 0)
                hours, remainder = divmod(int(uptime), 3600)
                minutes, seconds = divmod(remainder, 60)
                self.console.print(f"Uptime: [cyan]{hours}h {minutes}m {seconds}s[/cyan]")

                t = Table(box=box.SIMPLE)
                t.add_column("Endpoint", style="cyan")
                t.add_column("Status", style="bold")
                t.add_column("Polls", style="white")
                t.add_column("↑ MB", style="green")
                t.add_column("↓ MB", style="green")
                t.add_column("Avg RTT", style="yellow")
                t.add_column("Errors", style="red")
                for ep in diag.get("endpoints", []):
                    status = "[green]ALIVE[/green]" if ep["alive"] else "[red]DEAD[/red]"
                    t.add_row(
                        ep["id"],
                        status,
                        str(ep["polls"]),
                        f"{ep['bytes_sent'] / 1024 / 1024:.1f}",
                        f"{ep['bytes_recv'] / 1024 / 1024:.1f}",
                        f"{ep['avg_rtt_ms']}ms",
                        str(ep["failures"]),
                    )
                self.console.print(t)

            if self._pool_ref:
                self.console.print(f"Active sessions: [cyan]{self._pool_ref.active_count()}[/cyan]")
                tx_buf = self._pool_ref.tx_buffered_bytes()
                self.console.print(f"Tx buffer: [cyan]{tx_buf / 1024:.1f} KB[/cyan]")
                # Show quality scores
                t2 = Table(box=box.SIMPLE)
                t2.add_column("Endpoint", style="cyan")
                t2.add_column("Quality", style="bold")
                t2.add_column("Batch", style="white")
                t2.add_column("Avg RTT", style="yellow")
                for ep in diag.get("endpoints", []):
                    q = ep.get("quality_score", 0)
                    qs = f"[green]{q:.0f}%[/green]" if q > 70 else f"[yellow]{q:.0f}%[/yellow]" if q > 40 else f"[red]{q:.0f}%[/red]"
                    t2.add_row(ep["id"], qs, str(ep.get("batch_size", 8)), f"{ep['avg_rtt_ms']}ms")
                self.console.print(t2)
        else:
            self.console.print("[yellow]Tunnel is STOPPED[/yellow]")
        _ = Prompt.ask("Press Enter to continue")

    async def _diagnostics(self):
        if not self.config:
            self.console.print("[red]Load a config first[/red]")
            _ = Prompt.ask("Press Enter to continue")
            return

        self._clear()
        self._print_banner()
        self.console.print("[bold yellow]Running pre-flight diagnostics...[/bold yellow]")

        issues: list[str] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            # 1. Config validation
            task = progress.add_task("[cyan]Validating config...", total=1)
            cfg = self.config
            if not cfg.tunnel_key or len(cfg.tunnel_key) != 64:
                issues.append("tunnel_key missing or wrong length (need 64 hex chars)")
            try:
                bytes.fromhex(cfg.tunnel_key)
            except ValueError:
                issues.append("tunnel_key contains non-hex characters")
            if not cfg.script_keys:
                issues.append("no script_keys configured")
            for i, entry in enumerate(cfg.script_keys):
                if not entry.id.startswith("AKfycb"):
                    issues.append(f"script_keys[{i}]: doesn't start with 'AKfycb'")
            progress.update(task, completed=1)

            # 2. SOCKS5 port check
            task = progress.add_task("[cyan]Checking SOCKS5 port...", total=1)
            import socket as sockmod
            s = sockmod.socket(sockmod.AF_INET, sockmod.SOCK_STREAM)
            s.settimeout(1.0)
            result = s.connect_ex((cfg.socks_host, cfg.socks_port))
            s.close()
            if result == 0:
                issues.append(f"Port {cfg.socks_port} already in use")
            progress.update(task, completed=1)

            # 3. DNS resolution
            task = progress.add_task("[cyan]Resolving Google edge IP...", total=1)
            try:
                import socket as sockmod
                addrs = sockmod.getaddrinfo(cfg.google_host, 443)
                self.console.print(f"  [dim]Resolved {cfg.google_host} -> {addrs[0][4][0]}[/dim]")
            except OSError as e:
                issues.append(f"Cannot resolve {cfg.google_host}: {e}")
            progress.update(task, completed=1)

            # 4. TLS check
            task = progress.add_task("[cyan]Testing HTTPS reachability...", total=1)
            try:
                import aiohttp
                connector = aiohttp.TCPConnector()
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(
                        f"https://{cfg.sni_hosts[0]}/",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        self.console.print(f"  [dim]HTTPS OK: HTTP {resp.status}[/dim]")
            except Exception as e:
                issues.append(f"HTTPS reachability failed: {e}")
            progress.update(task, completed=1)

        # Results
        if not issues:
            self.console.print("\n[bold green]All checks passed - system is ready.[/bold green]")
        else:
            self.console.print(f"\n[bold red]Found {len(issues)} issue(s):[/bold red]")
            for i, issue in enumerate(issues, 1):
                self.console.print(f"  {i}. {issue}")
            self.console.print("\n[yellow]Fix the issues above before starting the tunnel.[/yellow]")

        _ = Prompt.ask("Press Enter to continue")

    def _setup_wizard(self):
        """First-time setup wizard."""
        self._clear()
        self._print_banner()
        self.console.print("[bold green]=== First-time Setup Wizard ===[/bold green]\n")
        self.console.print("I'll help you create a client_config.json step by step.\n")

        cfg = ClientConfig()
        self.config = cfg

        # 1. SOCKS5 settings
        self.console.print("[bold]Step 1: SOCKS5 Proxy Settings[/bold]")
        cfg.socks_host = Prompt.ask("  Listen host", default="127.0.0.1")
        cfg.socks_port = int(Prompt.ask("  Listen port", default="1080"))
        if Confirm.ask("  Enable SOCKS5 auth?", default=False):
            cfg.socks_user = Prompt.ask("  Username")
            cfg.socks_pass = Prompt.ask("  Password", password=True)

        # 2. Domain fronting
        self.console.print("\n[bold]Step 2: Domain Fronting Settings[/bold]")
        self.console.print("  (Leave defaults unless you know what you're doing)")

        cfg.google_host = Prompt.ask("  Google edge IP", default="216.239.38.120")
        sni_default = ",".join(cfg.sni_hosts)
        sni_str = Prompt.ask("  SNI host(s) (comma-sep)", default=sni_default)
        cfg.sni_hosts = [h.strip() for h in sni_str.split(",") if h.strip()]

        # 3. Script keys
        self.console.print("\n[bold]Step 3: Apps Script Deployments[/bold]")
        self.console.print("  Enter one or more deployment IDs (from Deploy > New deployment)")
        while True:
            did = Prompt.ask("  Deployment ID (or empty to finish)")
            if not did:
                break
            acct = Prompt.ask("  Account label (optional)", default="")
            cfg.script_keys.append(ScriptKeyEntry(id=did.strip(), account=acct.strip()))
            self.console.print(f"  [green]Added: {did[:20]}...[/green]")

        if not cfg.script_keys:
            self.console.print("[red]You need at least one deployment ID![/red]")
            _ = Prompt.ask("Press Enter to continue")
            return

        # 4. Tunnel key
        self.console.print("\n[bold]Step 4: Tunnel Key (AES-256-GCM)[/bold]")
        if Confirm.ask("  Generate a random key?", default=True):
            import secrets
            cfg.tunnel_key = secrets.token_hex(32)
            self.console.print(f"  [green]Generated: {cfg.tunnel_key[:16]}...{cfg.tunnel_key[-16:]}[/green]")
        else:
            key = Prompt.ask("  Enter 64-char hex key", password=True)
            cfg.tunnel_key = key

        # 5. Performance
        self.console.print("\n[bold]Step 5: Performance Tuning[/bold]")
        cfg.coalesce_step_ms = int(Prompt.ask("  coalesce_step_ms (20-40ms)", default="25"))
        cfg.idle_slots_per_bucket = int(Prompt.ask("  idle_slots_per_bucket (0-3)", default="2"))

        # Save
        path = Prompt.ask("\nSave config to", default=self.config_path)
        self.config_path = path
        data = {
            "debug_timing": cfg.debug_timing,
            "socks_host": cfg.socks_host,
            "socks_port": cfg.socks_port,
            "socks_user": cfg.socks_user,
            "socks_pass": cfg.socks_pass,
            "google_host": cfg.google_host,
            "sni": cfg.sni_hosts,
            "script_keys": [
                {"id": e.id, "account": e.account} if e.account else e.id
                for e in cfg.script_keys
            ],
            "tunnel_key": cfg.tunnel_key,
            "coalesce_step_ms": cfg.coalesce_step_ms,
            "idle_slots_per_bucket": cfg.idle_slots_per_bucket,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        self.console.print(f"\n[bold green]Config saved to {path}![/bold green]")
        self.console.print("[green]You can now start the tunnel (option 1).[/green]")
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
            self.config = load_client_config(path)
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
        path = Prompt.ask("Export to path", default="astrix_export.json")
        data = {
            "debug_timing": self.config.debug_timing,
            "socks_host": self.config.socks_host,
            "socks_port": self.config.socks_port,
            "socks_user": self.config.socks_user,
            "socks_pass": self.config.socks_pass,
            "google_host": self.config.google_host,
            "sni": self.config.sni_hosts,
            "script_keys": [
                {"id": e.id, "account": e.account} if e.account else e.id
                for e in self.config.script_keys
            ],
            "tunnel_key": self.config.tunnel_key,
            "coalesce_step_ms": self.config.coalesce_step_ms,
            "idle_slots_per_bucket": self.config.idle_slots_per_bucket,
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
            "debug_timing": self.config.debug_timing,
            "socks_host": self.config.socks_host,
            "socks_port": self.config.socks_port,
            "socks_user": self.config.socks_user,
            "socks_pass": "***" if self.config.socks_pass else "",
            "google_host": self.config.google_host,
            "sni": self.config.sni_hosts,
            "script_keys": [
                {"id": e.id[:16] + "..." + e.id[-8:], "account": e.account} if e.account else e.id[:16] + "..." + e.id[-8:]
                for e in self.config.script_keys
            ],
            "tunnel_key": self.config.tunnel_key[:8] + "..." + self.config.tunnel_key[-8:],
            "coalesce_step_ms": self.config.coalesce_step_ms,
            "idle_slots_per_bucket": self.config.idle_slots_per_bucket,
        }
        self.console.print(Syntax(json.dumps(data, indent=2), "json"))
        _ = Prompt.ask("Press Enter to continue")

    def _show_about(self):
        self._clear()
        text = Text()
        text.append(f"Astrix Client v{__version__}\n", style="bold cyan")
        text.append("\nA TCP-over-Apps-Script censorship circumvention tool.\n")
        text.append("\nAuthor: ", style="bold")
        text.append("UNDEAD\n")
        text.append("GitHub: ", style="bold")
        text.append("https://github.com/itsund3ad\n")
        text.append("\nOptimized Python port of GooseRelayVPN.\n")
        text.append("Features: AES-256-GCM + Zstd, domain fronting,\n")
        text.append("multi-endpoint failover, auto-reconnect, Rich TUI.\n")
        self.console.print(Panel(text, border_style="cyan"))
        _ = Prompt.ask("Press Enter to continue")

    async def run(self):
        self._clear()
        self._print_banner()

        if os.path.exists(self.config_path):
            try:
                self.config = load_client_config(self.config_path)
                self.console.print(f"[dim]Auto-loaded config from {self.config_path}[/dim]")
            except Exception as e:
                self.console.print(f"[dim]Auto-load: {e}[/dim]")

        while True:
            self.console.print("")
            self._print_menu()
            choice = Prompt.ask("Choice", default="1").strip().lower()

            if choice == "1":
                await self._start_tunnel()
                self._clear()
                self._print_banner()
            elif choice == "2":
                self._config_menu()
                self._clear()
                self._print_banner()
            elif choice == "3":
                await self._show_status()
                self._clear()
                self._print_banner()
            elif choice == "4":
                await self._diagnostics()
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
                    self.console.print("[yellow]Stopping tunnel...[/yellow]")
                self.console.print("[cyan]Goodbye![/cyan]")
                return
