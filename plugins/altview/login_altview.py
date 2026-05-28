"""OpenAI OAuth login flow as an AltView plugin.

Runs the full device-code OAuth flow inside the alternate buffer:
  1. Request device code from OpenAI
  2. Display URL + user code, open browser
  3. Poll for authorization
  4. Exchange for tokens
  5. Query available models

The command handler pushes this view onto the stack, awaits it,
then reads the result from the `result` attribute.
"""

import asyncio
import logging
import time
from typing import Any, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class LoginAltView(AltView):
    """AltView for the OpenAI OAuth device-code login flow.

    Stages:
        init         requesting device code
        device_code  code received, opening browser
        waiting      polling for user to authorize in browser
        models       authenticated, querying available models
        done         flow complete (auto-exits)
        error        something went wrong (shows message, Esc exits)
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="login",
            description="OpenAI OAuth login",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[KEY]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 10.0
        self.render_on_timer = True

        # OAuth state
        self._stage: str = "init"
        self._url: str = ""
        self._code: str = ""
        self._error_message: str = ""
        self._cancelled: bool = False

        # Result data -- read by the command handler after push() returns
        self.result_tokens: Optional[Any] = None
        self.result_models: list = []
        self.result_best_model: str = ""
        self.result_error: Optional[str] = None

        # Spinner for waiting stage
        self._spinner_chars = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._last_spinner_time = 0.0

    async def on_enter(self, renderer: Any) -> None:
        """Start the OAuth flow as a background task."""
        self._renderer = renderer
        self._stage = "init"
        self._cancelled = False
        self.spawn_background_task(self._run_oauth_flow(), name="oauth-flow")
        logger.info("LoginAltView: entered, OAuth flow started")

    async def render_frame(self, delta_time: float) -> bool:
        """Render the login screen. Returns False when done or cancelled."""
        if not self._renderer:
            return False

        if self._stage == "done":
            return False

        if self._cancelled:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()

        self._renderer.clear_screen()
        self._render_content(width, height, theme)

        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Esc cancels the login flow."""
        if key_press.name == "Escape" or key_press.char == "q":
            self._cancelled = True
            self.result_error = "cancelled by user"
            logger.info("LoginAltView: cancelled by user")
            return True

        # In error state, any key exits
        if self._stage == "error":
            return True

        return False

    async def on_complete(self) -> None:
        """Clean up background tasks."""
        await super().on_complete()

    # -- rendering helpers --

    def _render_content(self, width: int, height: int, theme: Any) -> None:
        """Render the appropriate stage content."""
        assert self._renderer is not None  # guarded by render_frame
        content_height = 18
        top_pad = max(1, (height - content_height) // 2)

        # Header bar
        header = " OpenAI OAuth Login "
        self._renderer.write_at(
            0,
            top_pad - 1,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        self._renderer.write_at(
            0,
            top_pad,
            solid(header.ljust(width), theme.dark[1], theme.text, width),
            "",
        )

        y = top_pad + 2

        # Prerequisites block
        prereq_lines = [
            "prerequisite: enable device code auth",
            "  1. go to chatgpt.com",
            "  2. settings > security (or data controls)",
            "  3. enable 'device code authorization'",
        ]
        for line in prereq_lines:
            self._renderer.write_at(2, y, line[: width - 4], "")
            y += 1

        y += 1

        # Stage-specific content
        if self._stage == "init":
            self._renderer.write_at(2, y, "requesting device code...", "")

        elif self._stage == "device_code":
            self._renderer.write_at(
                2, y, "device code received, opening browser...", ""
            )

        elif self._stage == "waiting":
            now = time.monotonic()
            if now - self._last_spinner_time > 0.3:
                self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_chars)
                self._last_spinner_time = now

            spinner = self._spinner_chars[self._spinner_idx]

            url_line = f"open:  {self._url}"
            self._renderer.write_at(2, y, url_line[: width - 4], "")
            y += 1
            # Highlight the code
            self._renderer.write_at(
                2,
                y,
                solid(
                    f"  {self._code}  ",
                    theme.primary[0],
                    theme.text_dark,
                    len(self._code) + 4,
                ),
                "",
            )
            y += 2
            self._renderer.write_at(
                2,
                y,
                f"{spinner} waiting for browser authorization...",
                "",
            )
            y += 1
            self._renderer.write_at(
                2,
                y,
                "(complete login in your browser)",
                "",
            )

        elif self._stage == "models":
            self._renderer.write_at(
                2, y, "authenticated, querying available models...", ""
            )

        elif self._stage == "error":
            self._renderer.write_at(2, y, f"error: {self._error_message}", "")
            y += 2
            self._renderer.write_at(2, y, "press any key to exit", "")

        # Footer with escape hint
        footer_y = height - 2
        self._renderer.write_at(
            0,
            footer_y,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        footer_text = " Esc: cancel"
        if self._stage == "error":
            footer_text = " any key: exit"
        self._renderer.write_at(
            0,
            footer_y + 1,
            solid(
                footer_text.ljust(width),
                theme.dark[1],
                theme.text_dim,
                width,
            ),
            "",
        )

    # -- OAuth flow --

    async def _run_oauth_flow(self) -> None:
        """Execute the full OAuth device-code flow.

        Updates self._stage as it progresses. On success, populates
        the result_* attributes. On failure, sets result_error and
        transitions to the error stage.
        """
        client = None
        try:
            from kollabor_ai.oauth import OAuthTokenStorage, OpenAIOAuthClient
            from kollabor_ai.oauth.openai_oauth import (
                DEVICE_VERIFY_URL,
                pick_best_model,
                query_codex_models,
            )

            client = OpenAIOAuthClient()
            storage = OAuthTokenStorage()

            # Step 1: Request device code
            self._stage = "init"
            device = await client._request_device_code()

            # Step 2: Open browser
            self._stage = "device_code"
            self._url = DEVICE_VERIFY_URL
            self._code = device.user_code

            import webbrowser

            try:
                webbrowser.open(DEVICE_VERIFY_URL)
            except Exception:
                pass

            # Brief pause so user sees the device_code stage
            await asyncio.sleep(0.3)

            # Step 3: Poll for authorization
            self._stage = "waiting"
            auth_resp = await client._poll_for_auth_code(device)

            # Step 4: Exchange for tokens
            tokens = await client._exchange_code(
                auth_resp.authorization_code,
                auth_resp.code_verifier,
            )

            await client.close()
            client = None

            # Store tokens
            await storage.store_tokens("openai", tokens)

            # Step 5: Query available models
            self._stage = "models"
            available_models = await query_codex_models(
                tokens.access_token, tokens.account_id
            )
            model = pick_best_model(available_models)

            # Populate results for the command handler
            self.result_tokens = tokens
            self.result_models = available_models
            self.result_best_model = model
            self.result_error = None

            logger.info("LoginAltView: OAuth flow complete, model=%s", model)

            # Signal done
            self._stage = "done"

        except asyncio.CancelledError:
            self._stage = "done"
            self.result_error = "cancelled"

        except Exception as e:
            logger.error("LoginAltView: OAuth flow failed: %s", e)
            self._error_message = str(e)
            self._stage = "error"
            self.result_error = str(e)

        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
