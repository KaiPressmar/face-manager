"""Generate and validate photo filenames that include recognized people."""

import os
import re

DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR = " "
DEFAULT_FILENAME_PERSON_JOINER = ", "


def build_filename_person_format_summary(
    joiner: str | None = None,
    block_separator: str | None = None,
) -> str:
    """Return a human-readable format string for UI display and compatibility."""
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    names_placeholder = f"Name 1{joiner}Name 2"
    return f"DATEI{block_separator}{names_placeholder}.jpg"


def _dedupe_names_in_order(names):
    """Keep the first occurrence of each non-empty name."""
    seen = set()
    ordered = []
    for name in names:
        normalized = (name or "").strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _extract_trailing_person_names(
    stem: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
):
    """Split a filename stem into root text and a trailing person appendix."""
    if not stem or not detected_names:
        return stem, []

    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    if not block_separator:
        return stem, []

    normalized_names = _dedupe_names_in_order(detected_names)
    if not normalized_names:
        return stem, []

    known_names = {name.casefold(): name for name in normalized_names}
    name_patterns = sorted(normalized_names, key=len, reverse=True)
    separator_pattern = re.compile(r"[\s,;:+&/|()[\]{}._-]+")

    def _parse_suffix_names(suffix_text: str):
        stripped_suffix = suffix_text.strip()
        if not stripped_suffix:
            return []

        suffix_names = []
        position = 0
        while position < len(stripped_suffix):
            matched_name = None
            for candidate in name_patterns:
                candidate_length = len(candidate)
                if stripped_suffix[position : position + candidate_length].casefold() == (
                    candidate.casefold()
                ):
                    matched_name = known_names[candidate.casefold()]
                    suffix_names.append(matched_name)
                    position += candidate_length
                    break
            if matched_name is None:
                return []
            if position >= len(stripped_suffix):
                return suffix_names
            if not joiner or stripped_suffix[position : position + len(joiner)].casefold() == joiner.casefold():
                position += len(joiner)
                continue
            separator_match = separator_pattern.match(stripped_suffix, position)
            if separator_match is None:
                return []
            position = separator_match.end()

    search_from = 0
    while True:
        separator_index = stem.find(block_separator, search_from)
        if separator_index < 0:
            return stem, []

        suffix_text = stem[separator_index + len(block_separator) :]
        if suffix_text:
            suffix_names = _parse_suffix_names(suffix_text)
            root_stem = stem[:separator_index]
            if suffix_names and root_stem:
                return root_stem, suffix_names

        search_from = separator_index + len(block_separator)


def build_person_filename_preview(
    filename: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
    *,
    filename_limit: int | None = None,
):
    """Build a rename preview for one filename based on detected people."""
    ordered_names = _dedupe_names_in_order(detected_names)
    if not ordered_names:
        return None
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner

    stem, extension = os.path.splitext(filename)
    names_text = joiner.join(ordered_names)
    if not block_separator and stem.endswith(names_text):
        return None
    root_stem, current_suffix_names = _extract_trailing_person_names(
        stem,
        ordered_names,
        block_separator=block_separator,
        joiner=joiner,
    )
    if not root_stem:
        root_stem = stem

    next_filename = f"{root_stem}{block_separator}{names_text}".strip() + extension
    validation_error = None
    omitted_person_count = 0
    if filename_limit is not None:
        validation_error = filename_validation_error(next_filename, filename_limit)
        if validation_error == "filename_too_long" and block_separator:
            # Keep the original text and extension intact. Only propose whole
            # names. A separator lets later previews recognize the appendix.
            shortened_stem = f"{root_stem}{block_separator}"
            for index, name in enumerate(ordered_names):
                shortened_stem += f"{joiner}{name}" if index else name
                shortened = shortened_stem.strip() + extension
                if filename_validation_error(shortened, filename_limit):
                    break
                next_filename = shortened
                omitted_person_count = len(ordered_names) - index - 1
                validation_error = None

    if next_filename == filename and (
        current_suffix_names == ordered_names or omitted_person_count
    ):
        return None

    return {
        "current_filename": filename,
        "proposed_filename": next_filename,
        "detected_person_names": ordered_names,
        "current_suffix_person_names": current_suffix_names,
        "validation_error": validation_error,
        "omitted_person_count": omitted_person_count,
    }


def filename_validation_error(filename: str, maximum: int) -> str | None:
    """Validate the final basename in the filesystem's filename units."""
    forbidden = '/\\\x00'
    if os.name == "nt":
        forbidden += '<>:"|?*'
    if not filename or filename in {".", ".."} or any(char in forbidden for char in filename):
        return "invalid_filename"
    if os.name == "nt":
        stem = filename.split(".", 1)[0].rstrip().upper()
        if (
            filename.endswith((".", " "))
            or any(ord(char) < 32 for char in filename)
            or re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", stem)
        ):
            return "invalid_filename"
        units = len(filename.encode("utf-16-le", errors="surrogatepass")) // 2
    else:
        units = len(os.fsencode(filename))
    return "filename_too_long" if maximum >= 0 and units > maximum else None
