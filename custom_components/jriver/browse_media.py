"""Support for media browsing."""

import contextlib
import logging

from hamcws import (
    BrowsePath,
    MediaServer,
    MediaSubType,
    MediaType as mc_MediaType,
    search_for_path,
)

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaType,
)
from homeassistant.core import HomeAssistant

from . import _translate_to_media_class, _translate_to_media_type
from .const import MC_FIELD_TO_HA_MEDIACLASS, MC_FIELD_TO_HA_MEDIATYPE

_LOGGER = logging.getLogger(__name__)


class UnknownMediaType(BrowseError):
    """Unknown media type."""


def media_source_content_filter(item: BrowseMedia) -> bool:
    """Content filter for media sources."""
    # MK media-source
    return not (
        item.media_content_id.startswith("media-source://camera/")
        and item.media_content_type == "image/png"
    )


def _format_item_name(values: dict) -> str:
    mt = _decode_media_type(values)
    if mt:
        if mt == MediaType.EPISODE:
            return f'{values["Episode"]}: {values["Name"]}'
        if mt == MediaType.TRACK and "Track #" in values:
            return f'{values["Track #"]}: {values["Name"]}'
        if mt == MediaType.MOVIE:
            if "HDR Format" in values:
                return f'{values["Name"]} (HDR)'
    return values["Name"]


def _decode_media_type(item: dict) -> MediaType | str:
    return _translate_to_media_type(
        item.get("Media Type", ""), item.get("Media Sub Type", ""), single=True
    )


def _decode_media_class(item: dict) -> MediaClass | str:
    return _translate_to_media_class(
        item.get("Media Type", ""), item.get("Media Sub Type", ""), single=True
    )


async def browse_nodes(
    hass: HomeAssistant,
    ms: MediaServer,
    browse_paths: list[BrowsePath],
    parent_content_type: str | None = None,
    parent_id: str = "-1",
) -> tuple[BrowseMedia, int]:
    """Create a BrowseMedia containing the children of the specified base_id."""
    if not parent_id:
        parent_id = "-1"
    parent_media_id = parent_id
    mt: MediaType | str | None
    mc: MediaClass | str | None
    container_media_class: MediaClass = MediaClass.DIRECTORY
    container_media_type: MediaType | str = "library"

    if parent_id == "-1":
        parent_name = None
        path_tokens = []
    elif parent_id.startswith("N|"):
        _, parent_id, parent_name = parent_id.split("|", 3)
        path_tokens = parent_name.split(" > ")
        if parent_content_type:
            container_media_type = parent_content_type
        browse_path = search_for_path(browse_paths, path_tokens)
        if browse_path:
            classification = _classify_browse_path(browse_path)
            if classification:
                container_media_class = classification[0]
                container_media_type = str(classification[1])
        elif path_tokens[0] == "Playlists":
            container_media_class = MediaClass.PLAYLIST
            container_media_type = MediaType.PLAYLIST
    else:
        raise ValueError(f"Unknown media_content_id format {parent_id}")

    is_child: bool = parent_name is not None

    nodes = await ms.browse_children(base_id=int(parent_id))
    items: list[dict[str, str]]
    expandable: bool
    if nodes:
        items = []
        for name, node_id in nodes.items():
            child_path = [*path_tokens, name]
            if container_media_class == MediaClass.PLAYLIST:
                mt = container_media_type
                mc = container_media_class
            else:
                browse_path = search_for_path(browse_paths, child_path)
                if not browse_path:
                    continue
                classification = _classify_browse_path(browse_path)
                if classification:
                    mc, mt = classification
                else:
                    mc = container_media_class
                    try:
                        mt = MediaType[container_media_type]
                    except KeyError:
                        mt = container_media_type
            vals = {
                "id": node_id,
                "media_id": f"N|{node_id}|{' > '.join(child_path)}",
                "name": name,
                "thumbnail": await ms.get_browse_thumbnail_url(node_id),
                "mt": mt,
                "mc": mc,
            }
            items.append(vals)
        expandable = len(items) > 0
    else:
        files = await ms.browse_files(base_id=int(parent_id))
        items = [
            {
                "id": file["Key"],
                "media_id": f'K|{file["Key"]}',
                "name": _format_item_name(file),
                "thumbnail": await ms.get_file_image_url(int(file["Key"])),
                "mt": _decode_media_type(file),
                "mc": _decode_media_class(file),
            }
            for file in files
        ]
        expandable = False

    children: list[BrowseMedia] = [
        BrowseMedia(
            title=item["name"],
            media_class=item["mc"],
            media_content_type=item["mt"],
            media_content_id=item["media_id"],
            can_play=is_child,
            can_expand=expandable,
            thumbnail=item["thumbnail"],
        )
        for item in items
    ]
    count = len(children)
    library_info = BrowseMedia(
        media_class=container_media_class,
        media_content_id=parent_media_id,
        media_content_type=container_media_type,
        title=parent_name if parent_name else "Media Library",
        can_play=not expandable,
        can_expand=expandable,
        children=children,
    )
    # add HA provided nodes to the initial browse only
    if is_child is False:
        with contextlib.suppress(media_source.BrowseError):
            item = await media_source.async_browse_media(
                hass, None, content_filter=media_source_content_filter
            )
            # If domain is None, it's overview of available sources
            if item.domain is None:
                if item.children:
                    children = [*children, *item.children]
            else:
                children.append(item)

    return library_info, count


def _classify_browse_path(path: BrowsePath) -> tuple[MediaClass, MediaType] | None:
    def _translate(
        mc_mt: mc_MediaType, mc_mst: MediaSubType | None
    ) -> tuple[MediaClass, MediaType] | None:
        mt: MediaType | str = _translate_to_media_type(mc_mt, mc_mst)
        mc: MediaClass | str = _translate_to_media_class(mc_mt, mc_mst)
        if isinstance(mt, MediaType) and isinstance(mc, MediaClass):
            return mc, mt
        return None

    mt = MC_FIELD_TO_HA_MEDIATYPE.get(path.name, None)
    mc = MC_FIELD_TO_HA_MEDIACLASS.get(path.name, None)
    if mt and mc:
        return MediaClass[mc], MediaType[mt]

    for mc_mt in path.effective_media_types:
        if path.effective_media_sub_types:
            for mst in path.effective_media_sub_types:
                vals = _translate(mc_mt, mst)
                if vals:
                    return vals
        else:
            vals = _translate(mc_mt, None)
            if vals:
                return vals

    return None
