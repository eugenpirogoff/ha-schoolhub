"""Config flow: one entry per SWOP login and per Drei Köche login."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
)
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    DreiKoecheClient,
    SchoolhubApiError,
    SchoolhubAuthError,
    SwopClient,
    normalize_url,
)
from .const import (
    CONF_SERVICE,
    CONF_WEEKS_FUTURE,
    CONF_WEEKS_PAST,
    DEFAULT_SCHOOL_URL,
    DEFAULTS,
    DOMAIN,
    KNOWN_SCHOOLS,
    SERVICE_DREI_KOECHE,
    SERVICE_SWOP,
)
from .entity import school_label
from .models import parse_drei_koeche_student, parse_students

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
USERNAME = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username"))


class SchoolhubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a SWOP or Drei Köche login."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SchoolhubOptionsFlow:
        """Return the options flow."""
        return SchoolhubOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user pick the service."""
        return self.async_show_menu(
            step_id="user", menu_options=[SERVICE_SWOP, SERVICE_DREI_KOECHE]
        )

    async def async_step_swop(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a SWOP login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors = {CONF_URL: "invalid_url"}
            else:
                if _is_ip_address(url):
                    # A school portal has a host name; IP addresses (often in
                    # the local network) are refused for new logins.
                    errors = {CONF_URL: "invalid_url"}
                elif not url.startswith("https://"):
                    # Existing http:// entries keep working, but a new login
                    # must not send the password unencrypted.
                    errors = {CONF_URL: "https_required"}
            if not errors:
                host = url.split("://", 1)[1]
                await self.async_set_unique_id(f"swop_{host}_{username.casefold()}")
                self._abort_if_unique_id_configured()
                errors, _ = await self._async_validate_swop(
                    url, username, user_input[CONF_PASSWORD]
                )
            if not errors:
                return self.async_create_entry(
                    title=f"SWOP {school_label(url)}",
                    data={
                        CONF_SERVICE: SERVICE_SWOP,
                        CONF_URL: url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="swop",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): self._school_selector(),
                        vol.Required(CONF_USERNAME): USERNAME,
                        vol.Required(CONF_PASSWORD): PASSWORD,
                    }
                ),
                user_input or {CONF_URL: DEFAULT_SCHOOL_URL},
            ),
            errors=errors,
        )

    def _school_selector(self) -> SelectSelector:
        """Offer known and already configured schools; others can be typed in."""
        urls = list(KNOWN_SCHOOLS)
        for entry in self._async_current_entries(include_ignore=False):
            if (url := entry.data.get(CONF_URL)) and url not in urls:
                urls.append(url)
        return SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=url, label=school_label(url)) for url in urls],
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    async def async_step_drei_koeche(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a Drei Köche login (one per child)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(f"drei_koeche_{username.casefold()}")
            self._abort_if_unique_id_configured()
            errors, name = await self._async_validate_drei_koeche(
                username, user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_create_entry(
                    title=f"Drei Köche {name}",
                    data={
                        CONF_SERVICE: SERVICE_DREI_KOECHE,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="drei_koeche",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_USERNAME): USERNAME,
                        vol.Required(CONF_PASSWORD): PASSWORD,
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Ask for a new password after the service rejected the stored one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            if entry.data[CONF_SERVICE] == SERVICE_SWOP:
                errors, _ = await self._async_validate_swop(
                    entry.data[CONF_URL], entry.data[CONF_USERNAME], password
                )
            else:
                errors, _ = await self._async_validate_drei_koeche(
                    entry.data[CONF_USERNAME], password
                )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD}),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    async def _async_validate_swop(
        self, url: str, username: str, password: str
    ) -> tuple[dict[str, str], str]:
        """Log in and return errors and the names of the children found."""
        # A throwaway session with its own cookie jar. The connector is shared
        # with Home Assistant, so the session is detached rather than closed.
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
        )
        client = SwopClient(session, url, username, password)
        try:
            await client.async_login()
            students = parse_students(await client.async_get_mydata())
        except SchoolhubAuthError:
            return {"base": "invalid_auth"}, ""
        except SchoolhubApiError:
            return {"base": "cannot_connect"}, ""
        except Exception:
            _LOGGER.exception("Unexpected error while validating SWOP login")
            return {"base": "unknown"}, ""
        finally:
            session.detach()
        if not students:
            return {"base": "no_students"}, ""
        return {}, ", ".join(student.display_name for student in students)

    async def _async_validate_drei_koeche(
        self, username: str, password: str
    ) -> tuple[dict[str, str], str]:
        """Log in and return errors and the child's name."""
        # A throwaway session that stores no cookies, detached like the SWOP one.
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        client = DreiKoecheClient(session, username, password)
        try:
            user = await client.async_login()
        except SchoolhubAuthError:
            return {"base": "invalid_auth"}, ""
        except SchoolhubApiError:
            return {"base": "cannot_connect"}, ""
        except Exception:
            _LOGGER.exception("Unexpected error while validating Drei Köche login")
            return {"base": "unknown"}, ""
        finally:
            session.detach()
        return {}, parse_drei_koeche_student(user).display_name


class SchoolhubOptionsFlow(OptionsFlowWithReload):
    """Change how many weeks are fetched."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )
        defaults = DEFAULTS[self.config_entry.data[CONF_SERVICE]]
        weeks = NumberSelector(
            NumberSelectorConfig(min=0, max=12, step=1, mode=NumberSelectorMode.BOX)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_WEEKS_PAST): weeks,
                        vol.Required(CONF_WEEKS_FUTURE): weeks,
                    }
                ),
                {**defaults, **self.config_entry.options},
            ),
        )


def _is_ip_address(url: str) -> bool:
    """Return whether the address uses an IP literal instead of a host name."""
    try:
        ipaddress.ip_address(urlsplit(url).hostname or "")
    except ValueError:
        return False
    return True
