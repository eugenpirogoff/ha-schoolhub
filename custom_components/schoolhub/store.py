"""State SchoolHub keeps in Home Assistant: ticked-off homework and seen news."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import NewsPost

STORAGE_VERSION = 1
SAVE_DELAY = 10
MAX_SEEN_PER_FEED = 100


def _key(entry_id: str) -> str:
    """Return the storage key of a config entry."""
    return f"{DOMAIN}.{entry_id}"


class LocalState:
    """Persisted per config entry; SWOP itself is never changed."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, _key(entry_id))
        self._done: set[str] = set()
        self._seen: dict[str, list[int]] = {}
        # Every child seen so far; older stored data has no such key.
        self._students: set[str] = set()

    async def async_load(self) -> None:
        """Load the stored state."""
        data = await self._store.async_load() or {}
        self._done = set(data.get("done", []))
        self._seen = {feed: list(ids) for feed, ids in data.get("seen", {}).items()}
        self._students = set(data.get("students", []))

    @staticmethod
    async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
        """Delete the stored state of a removed config entry."""
        await Store(hass, STORAGE_VERSION, _key(entry_id)).async_remove()

    async def async_flush(self) -> None:
        """Write pending changes now, e.g. before the entry unloads."""
        await self._store.async_save(self._data())

    def is_done(self, uid: str) -> bool:
        """Return whether a homework item was ticked off."""
        return uid in self._done

    def set_done(self, uid: str, done: bool) -> None:
        """Tick off or reopen a homework item."""
        if done:
            self._done.add(uid)
        else:
            self._done.discard(uid)
        self._schedule_save()

    def prune_done(self, students: set[str], known_uids: set[str]) -> None:
        """Forget ticked-off items that are no longer returned by SWOP.

        `students` are the children of the current refresh. A login that briefly
        returns fewer children (also right after a restart) must not erase the
        ticks of the missing ones, so items are only forgotten when every child
        seen so far is present.
        """
        if not students:
            return
        if students - self._students:
            self._students |= students
            self._schedule_save()
        if self._students <= students and self._done - known_uids:
            self._done &= known_uids
            self._schedule_save()

    def new_posts(self, feed: str, posts: list[NewsPost]) -> list[NewsPost]:
        """Return posts not seen before in a feed and mark them as seen.

        The first time a feed is seen, its current posts become the baseline,
        so installing the integration does not report old posts as new.
        """
        ids = [post.post_id for post in posts]
        if feed not in self._seen:
            self._seen[feed] = ids[:MAX_SEEN_PER_FEED]
            self._schedule_save()
            return []
        seen = set(self._seen[feed])
        new = [post for post in posts if post.post_id not in seen]
        if new:
            self._seen[feed] = list(dict.fromkeys(ids + self._seen[feed]))[:MAX_SEEN_PER_FEED]
            self._schedule_save()
        return new

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {
            "done": sorted(self._done),
            "seen": self._seen,
            "students": sorted(self._students),
        }
