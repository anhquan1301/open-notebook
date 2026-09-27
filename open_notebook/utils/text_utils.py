"""
Text utilities for Open Notebook.
Extracted from main utils to avoid circular imports.
"""

import re
import unicodedata
from typing import Tuple

# Patterns for matching thinking content in AI responses
# Standard pattern: <think>...</think>
THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
# Pattern for malformed output: content</think> (missing opening tag)
THINK_PATTERN_NO_OPEN = re.compile(r"^(.*?)</think>", re.DOTALL)


def remove_non_ascii(text: str) -> str:
    """Remove non-ASCII characters from text."""
    return re.sub(r"[^\x00-\x7F]+", "", text)


def remove_non_printable(text: str) -> str:
    """Remove non-printable characters from text."""
    # Replace any special Unicode whitespace characters with a regular space
    text = re.sub(r"[\u2000-\u200B\u202F\u205F\u3000]", " ", text)

    # Replace unusual line terminators with a single newline
    text = re.sub(r"[\u2028\u2029\r]", "\n", text)

    # Remove control characters, except newlines and tabs
    text = "".join(
        char for char in text if unicodedata.category(char)[0] != "C" or char in "\n\t"
    )

    # Replace non-breaking spaces with regular spaces
    text = text.replace("\xa0", " ").strip()

    # Keep letters (including accented ones), numbers, spaces, newlines, tabs, and basic punctuation
    return re.sub(r"[^\w\s.,!?\-\n\t]", "", text, flags=re.UNICODE)


def parse_thinking_content(content: str) -> Tuple[str, str]:
    """
    Parse message content to extract thinking content from <think> tags.

    Handles both well-formed tags and malformed output where the opening
    <think> tag is missing but </think> is present.

    Args:
        content (str): The original message content

    Returns:
        Tuple[str, str]: (thinking_content, cleaned_content)
            - thinking_content: Content from within <think> tags
            - cleaned_content: Original content with <think> blocks removed

    Example:
        >>> content = "<think>Let me analyze this</think>Here's my answer"
        >>> thinking, cleaned = parse_thinking_content(content)
        >>> print(thinking)
        "Let me analyze this"
        >>> print(cleaned)
        "Here's my answer"
    """
    # Input validation
    if not isinstance(content, str):
        return "", str(content) if content is not None else ""

    # Limit processing for very large content (100KB limit)
    if len(content) > 100000:
        return "", content

    # Find all well-formed thinking blocks
    thinking_matches = THINK_PATTERN.findall(content)

    if thinking_matches:
        # Join all thinking content with double newlines
        thinking_content = "\n\n".join(match.strip() for match in thinking_matches)

        # Remove all <think>...</think> blocks from the original content
        cleaned_content = THINK_PATTERN.sub("", content)

        # Clean up extra whitespace
        cleaned_content = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned_content).strip()

        return thinking_content, cleaned_content

    # Handle malformed output: content</think> (missing opening tag)
    # Some models like Nemotron output thinking without the opening <think> tag
    malformed_match = THINK_PATTERN_NO_OPEN.match(content)
    if malformed_match:
        thinking_content = malformed_match.group(1).strip()
        # Remove the thinking content and </think> tag
        cleaned_content = content[malformed_match.end() :].strip()
        return thinking_content, cleaned_content

    # Handle truncated output: <think>content (no closing tag). The model ran
    # out of output budget while still reasoning, so nothing is a real answer.
    stripped = content.lstrip()
    if stripped.startswith("<think>"):
        return stripped[len("<think>") :].strip(), ""

    return "", content


def clean_thinking_content(content: str) -> str:
    """
    Remove thinking content from AI responses, returning only the cleaned content.

    This is a convenience function for cases where you only need the cleaned
    content and don't need access to the thinking process.

    Args:
        content (str): The original message content with potential <think> tags

    Returns:
        str: Content with <think> blocks removed and whitespace cleaned

    Example:
        >>> content = "<think>Let me think...</think>Here's the answer"
        >>> clean_thinking_content(content)
        "Here's the answer"
    """
    _, cleaned_content = parse_thinking_content(content)
    return cleaned_content


def extract_text_content(content) -> str:
    """Extract text from LLM response content.

    Handles both plain string responses and structured content formats
    (e.g. Gemini's envelope format):
    [{'type': 'text', 'text': '...', 'extras': {...}}]

    Args:
        content: The content from an AI message, either a string or a list of parts.

    Returns:
        The extracted text content as a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                text_parts.append(part["text"])
            elif isinstance(part, str):
                text_parts.append(part)
        return "".join(text_parts)
    return str(content)


def clean_markdown_content(text: str) -> str:
    """Clean up bloated markdown content (especially from Excel / spreadsheet exports).

    1. Preserves code blocks (```...```) intact.
    2. Merges multi-line table cells (broken by raw newlines in Excel cells) using <br>.
    3. Drops completely empty rows (rows containing only pipes and whitespace).
    4. Automatically detects single-cell rows (titles, section headers, workflow descriptions)
       and formats them as clean Markdown headings (### Step) or bullet items.
    5. Formats multi-cell data rows as valid, uniform GFM tables with matching column counts
       and standard header separators (| --- |).
    6. Collapses excessive consecutive blank lines (> 2 newlines).
    """
    if not text:
        return text

    lines = text.split("\n")

    # Pass 1: Join broken table rows caused by raw newlines inside Excel cells
    merged_lines = []
    in_code = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code = not in_code
            merged_lines.append(line)
            continue

        if in_code:
            merged_lines.append(line)
            continue

        prev_stripped = merged_lines[-1].strip() if merged_lines else ""
        is_unclosed_table_row = (
            prev_stripped.startswith("|")
            and not prev_stripped.endswith("|")
            and not prev_stripped.startswith(">")
            and not re.match(r"^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$", prev_stripped)
        )

        if (
            is_unclosed_table_row
            and stripped
            and not stripped.startswith("#")
            and not stripped.startswith(">")
            and not stripped.startswith("|")
        ):
            merged_lines[-1] = merged_lines[-1].rstrip() + "<br>" + stripped
        else:
            merged_lines.append(line)

    # Pass 2: Process table blocks while preserving code blocks
    output = []
    table_block = []
    in_code_block = False

    def flush_table_smart(block):
        if not block:
            return []

        # Check if block contains an existing valid separator line (| --- |)
        sep_idx = None
        for idx, line in enumerate(block):
            stripped = line.strip()
            if re.match(r"^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$", stripped):
                sep_idx = idx
                break

        if sep_idx is not None and sep_idx > 0:
            # Existing structured markdown table: preserve its schema and integrity intact
            header_line = block[sep_idx - 1].strip()
            header_cells = [p.strip() for p in re.split(r"(?<!\\)\|", header_line)[1:-1]]
            target_cols = len(header_cells)
            if target_cols >= 1:
                res_table = [
                    "| " + " | ".join(header_cells) + " |",
                    "| " + " | ".join(["---"] * target_cols) + " |",
                ]
                for idx, line in enumerate(block):
                    if idx in (sep_idx - 1, sep_idx):
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    parts = re.split(r"(?<!\\)\|", stripped)
                    if len(parts) >= 2:
                        cells = [p.strip() for p in parts[1:-1]]
                    else:
                        cells = [stripped]
                    if not any(cells):
                        continue
                    if len(cells) < target_cols:
                        cells = cells + [""] * (target_cols - len(cells))
                    elif len(cells) > target_cols:
                        cells = cells[:target_cols]
                    res_table.append("| " + " | ".join(cells) + " |")
                return ["\n" + "\n".join(res_table) + "\n"]

        rows_data = []
        for line in block:
            stripped = line.strip()
            # Skip standalone separator lines, standard ones will be emitted
            if re.match(r"^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$", stripped):
                continue
            parts = re.split(r"(?<!\\)\|", stripped)
            if len(parts) >= 2:
                cells = [p.strip() for p in parts[1:-1]]
            else:
                cells = [stripped]

            non_empty = [c for c in cells if c]
            if not non_empty:
                continue
            rows_data.append((cells, non_empty))

        if not rows_data:
            return []

        # Split into sub-groups: runs of single-cell rows vs runs of multi-cell rows
        subgroups = []
        curr_kind = None
        curr_items = []

        for cells, non_empty in rows_data:
            kind = "single" if len(non_empty) <= 1 else "multi"
            if kind != curr_kind:
                if curr_items:
                    subgroups.append((curr_kind, curr_items))
                curr_kind = kind
                curr_items = [(cells, non_empty)]
            else:
                curr_items.append((cells, non_empty))
        if curr_items:
            subgroups.append((curr_kind, curr_items))

        result = []
        for kind, items in subgroups:
            if kind == "single":
                for cells, non_empty in items:
                    content = non_empty[0]
                    if re.match(r"^[0-9]+[\.．]", content):
                        result.append(f"\n### {content}\n")
                    elif content.startswith("■") or content.startswith("【"):
                        result.append(f"\n#### {content}\n")
                    elif content.startswith("※") or content.startswith("・"):
                        result.append(f"- {content}")
                    else:
                        result.append(content)
            else:
                # Multi-cell data table: calculate column bounds to drop unused outer columns
                min_col = min(
                    min(i for i, c in enumerate(cells) if c)
                    for cells, non_empty in items
                )
                max_col = (
                    max(
                        max(i for i, c in enumerate(cells) if c)
                        for cells, non_empty in items
                    )
                    + 1
                )
                target_cols = max(max_col - min_col, 2)

                res_table = []
                header_emitted = False
                for cells, non_empty in items:
                    sliced = cells[min_col:max_col]
                    trimmed = sliced + [""] * max(0, target_cols - len(sliced))
                    res_table.append("| " + " | ".join(trimmed) + " |")
                    if not header_emitted:
                        res_table.append(
                            "| " + " | ".join(["---"] * target_cols) + " |"
                        )
                        header_emitted = True
                result.append("\n" + "\n".join(res_table) + "\n")

        return result

    for line in merged_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if table_block:
                output.extend(flush_table_smart(table_block))
                table_block = []
            in_code_block = not in_code_block
            output.append(line)
            continue

        if in_code_block:
            output.append(line)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            table_block.append(stripped)
        else:
            if table_block:
                output.extend(flush_table_smart(table_block))
                table_block = []
            output.append(line)

    if table_block:
        output.extend(flush_table_smart(table_block))

    res_str = "\n".join(output)
    res_str = re.sub(r"\n{3,}", "\n\n", res_str)
    return res_str.strip()


