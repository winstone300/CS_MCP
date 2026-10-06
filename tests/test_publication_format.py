import pytest

from cs_study_mcp.publication import (
    PublicationFormatError,
    canonicalize_markdown,
    verify_readback,
)

DOCUMENT = """## 핵심 요약

- 공유 경계를 이해한다.

```mermaid
flowchart LR
    A[프로세스] --> B[스레드]
```

구조도 설명 [1](https://example.org).

| 개념 | 공유 범위 |
| --- | --- |
| 프로세스 | 별도 주소 공간 |
| 스레드 | 같은 프로세스 |

<details>
<summary>답안·해설</summary>

**답안:** 주소 공간을 공유한다.

**해설:** 프로세스 내부의 스레드끼리 공유한다.

</details>
"""


def test_readback_preserves_visual_table_toggle_and_blank_line_equivalence():
    expected = canonicalize_markdown(DOCUMENT)
    assert [block["type"] for block in expected] == ["heading", "list_item", "code", "paragraph", "table", "toggle"]
    actual = DOCUMENT.replace("\n\n", "\n\n\n").replace("\n", "\r\n")
    result = verify_readback(expected, actual)
    assert result["valid"]
    assert len(result["observed_hash"]) == 64


@pytest.mark.parametrize("changed", [
    DOCUMENT.replace("A[프로세스] --> B[스레드]", "A[프로세스] <-- B[스레드]"),
    DOCUMENT.replace("| 스레드 | 같은 프로세스 |", "| 스레드 | 다른 프로세스 |"),
    DOCUMENT.replace("**답안:** 주소 공간을 공유한다.", ""),
    DOCUMENT.replace("구조도 설명 [1](https://example.org).", ""),
    DOCUMENT.replace("| 프로세스 | 별도 주소 공간 |\n| 스레드 | 같은 프로세스 |", "| 스레드 | 같은 프로세스 |\n| 프로세스 | 별도 주소 공간 |"),
])
def test_missing_or_changed_content_cannot_be_normalized_away(changed):
    assert not verify_readback(canonicalize_markdown(DOCUMENT), changed)["valid"]


def test_forged_observed_blocks_do_not_override_markdown():
    expected = canonicalize_markdown(DOCUMENT)
    result = verify_readback(expected, "## 누락된 문서", observed_blocks=expected)
    assert not result["valid"]
    assert any("Supplied observed blocks" in error for error in result["errors"])


@pytest.mark.parametrize("text", [
    "```mermaid\nflowchart LR\nA-->B",
    "<details>\n<summary>답</summary>\n본문",
    "| A | B |\n| --- | --- |\n| missing |",
    '<table unknown="true"><tr><td>unsupported</td></tr></table>',
    "<image source=\"unknown\"/>",
    "text ![inline](https://example.org/image.png)",
])
def test_unsupported_or_malformed_syntax_fails_closed(text):
    with pytest.raises(PublicationFormatError):
        canonicalize_markdown(text)
    assert not verify_readback([], text)["valid"]


def test_code_and_list_indentation_are_preserved():
    expected = canonicalize_markdown("- A\n  - B\n\n```python\nif flag:\n    run()\n```")
    assert not verify_readback(expected, "- A\n- B\n\n```python\nif flag:\nrun()\n```")["valid"]
    assert canonicalize_markdown("## C#")[0]["text"] == "C#"


def test_notion_plain_text_alias_preserves_nested_code_readback():
    approved = (
        "<details>\n<summary>주제</summary>\n"
        "\t<details>\n\t<summary>의사코드</summary>\n"
        "\t\t```text\n\t\tif ready:\n\t\t    run()\n\t\t```\n"
        "\t</details>\n</details>"
    )
    expected = canonicalize_markdown(approved)
    observed = approved.replace("```text", "```plain text")
    result = verify_readback(expected, observed)
    assert result["valid"]
    assert result["observed_hash"] == verify_readback(expected, approved)["observed_hash"]
    assert not verify_readback(expected, observed.replace("run()", "stop()"))["valid"]
    assert not verify_readback(expected, observed.replace("    run()", "run()"))["valid"]
    assert not verify_readback(expected, observed.replace("plain text", "python"))["valid"]


def test_plain_text_alias_does_not_hide_mermaid_language_changes():
    approved = "```mermaid\nflowchart LR\nA-->B\n```"
    assert not verify_readback(
        canonicalize_markdown(approved), approved.replace("mermaid", "plain text")
    )["valid"]


def test_notion_escaped_numeric_range_in_nested_prose_matches_literal_range():
    approved = (
        "<details>\n<summary>모드</summary>\n"
        "\t<details>\n\t<summary>권한 수준</summary>\n"
        "\t\tIntel x86은 0~3의 네 privilege level을 정의한다.\n"
        "\t</details>\n</details>"
    )
    expected = canonicalize_markdown(approved)
    observed = approved.replace("0~3", r"0\~3")
    assert verify_readback(expected, observed)["valid"]
    assert verify_readback(expected, observed)["observed_hash"] == verify_readback(
        expected, approved,
    )["observed_hash"]
    assert not verify_readback(expected, observed.replace("3의", "4의"))["valid"]
    assert not verify_readback(expected, observed.replace(r"0\~3", r"0\\~3"))["valid"]


@pytest.mark.parametrize("approved", [
    "`0~3`", "```text\n0~3\n```", "```mermaid\nA[0~3]\n```",
    "[0~3](https://example.org/0~3)", "https://example.org/0~3", "~~0~3~~",
    "| 範囲 |\n| --- |\n| 0~3 |", "## 0~3",
    "<details>\n<summary>0~3</summary>\n本文\n</details>",
])
def test_range_escape_equivalence_does_not_change_code_links_or_other_blocks(approved):
    assert not verify_readback(
        canonicalize_markdown(approved), approved.replace("0~3", r"0\~3"),
    )["valid"]


def test_notion_nested_raw_code_and_table_payloads_preserve_contents():
    approved = (
        '<details>\n<summary>주제</summary>\n\t<details>\n\t<summary>내용</summary>\n'
        '\t\t```text\n\t\tif ready:\n\t\t    run()\n\t\t```\n'
        '\t\t<table header-row="true">\n\t\t\t<tr>\n'
        '\t\t\t\t<td>키</td>\n\t\t\t</tr>\n\t\t\t<tr>\n'
        '\t\t\t\t<td>값</td>\n\t\t\t</tr>\n\t\t</table>\n'
        '\t</details>\n</details>'
    )
    observed = approved.replace(
        '\t\t```text\n\t\tif ready:\n\t\t    run()',
        '\t\t```plain text\nif ready:\n    run()',
    ).replace('\t\t\t\t<td>', '<td>').replace('\t\t\t<tr>', '<tr>').replace(
        '\t\t\t</tr>', '</tr>',
    )
    expected = canonicalize_markdown(approved)
    assert verify_readback(expected, observed)["valid"]
    assert not verify_readback(expected, observed.replace('    run()', 'run()'))["valid"]
    assert not verify_readback(expected, observed.replace('    run()', '\trun()'))["valid"]
    assert not verify_readback(expected, observed.replace('\t\t```\n', '\t\t```\n빠진 구조 탭\n'))["valid"]
    assert not verify_readback(expected, observed.replace('<td>값</td>', '<td>변경</td>'))["valid"]
    assert not verify_readback(expected, observed.replace('<tr>\n<td>값</td>\n</tr>', ''))["valid"]


def test_notion_escaped_half_open_intervals_preserve_nested_prose_and_endpoints():
    approved = (
        "<details>\n<summary>스케줄링</summary>\n"
        "\t<details>\n\t<summary>RR 계산</summary>\n"
        "\t\tA의 ready 대기는 [2,4)와 [6,7), B의 ready 대기는 [0,2)와 [4,6)이다.\n"
        "\t</details>\n</details>"
    )
    expected = canonicalize_markdown(approved)
    observed = approved.replace("[", r"\[")
    result = verify_readback(expected, observed)
    assert result["valid"]
    assert result["observed_hash"] == verify_readback(expected, approved)["observed_hash"]
    assert not verify_readback(expected, observed.replace("2,4)", "2,5)"))["valid"]
    assert not verify_readback(expected, observed.replace("2,4)", "2,4]"))["valid"]
    assert not verify_readback(expected, observed.replace(r"\[2,4)", r"\\[2,4)"))["valid"]


@pytest.mark.parametrize("approved", [
    "`[2,4)`", "```text\n[2,4)\n```", "```mermaid\nA[2,4)\n```",
    "[구간](https://example.org) [2,4)", "https://example.org/[2,4)",
    "~~[2,4)~~", "| 구간 |\n| --- |\n| [2,4) |", "## [2,4)",
    "- [2,4)", "<details>\n<summary>[2,4)</summary>\n본문\n</details>",
    "다른 \\문자와 [2,4)",
])
def test_interval_escape_equivalence_keeps_code_links_and_other_blocks_opaque(approved):
    assert not verify_readback(
        canonicalize_markdown(approved), approved.replace("[2,4)", r"\[2,4)"),
    )["valid"]


def test_signed_image_urls_require_bytes_or_persisted_receipt():
    asset_hash = "a" * 64
    expected = canonicalize_markdown(f"![공식 화면](asset://{asset_hash})\n\n확인된 화면의 설명")
    approved = [{"hash": asset_hash, "remote_ref": "file-upload-1", "remote_url": "https://cdn.example/a?old"}]
    observed = "![공식 화면](https://cdn.example/a?new)\n\n확인된 화면의 설명"
    assert not verify_readback(expected, observed, expected_assets=approved)["valid"]
    assert not verify_readback(expected, observed, expected_assets=approved, observed_assets=[{"remote_url": "https://cdn.example/a?new"}])["valid"]
    receipt = {"remote_ref": "file-upload-1", "remote_url": "https://cdn.example/a?new"}
    assert verify_readback(expected, observed, expected_assets=approved, observed_assets=[receipt])["valid"]
    assert verify_readback(expected, observed, expected_assets=approved, observed_assets=[{"hash": asset_hash, "remote_url": "https://cdn.example/a?new"}])["valid"]
    # Byte mismatch overrides a correct receipt and image labels remain checked.
    assert not verify_readback(expected, observed, expected_assets=approved, observed_assets=[{**receipt, "hash": "b" * 64}])["valid"]
    assert not verify_readback(expected, observed.replace("공식 화면", "다른 화면"), expected_assets=approved, observed_assets=[receipt])["valid"]


def test_hash_in_image_url_is_not_proof_of_observed_asset():
    asset_hash = "c" * 64
    markdown = f"![화면](asset://{asset_hash})"
    assert not verify_readback(canonicalize_markdown(markdown), markdown, expected_assets=[{"hash": asset_hash}])["valid"]


@pytest.mark.parametrize("through_receipt", [False, True])
@pytest.mark.parametrize("reverse_evidence", [False, True])
def test_separate_conflicting_byte_observation_cannot_be_overridden_by_receipt(
    through_receipt, reverse_evidence,
):
    approved_hash = "a" * 64
    observed_url = "https://cdn.example/image?new"
    expected = canonicalize_markdown(f"![화면](asset://{approved_hash})")
    approved = [{"hash": approved_hash, "remote_ref": "upload-1"}]
    evidence = [
        {"remote_ref": "upload-1", "remote_url": observed_url},
        {"hash": "b" * 64, "remote_url": "https://cdn.example/image?old" if through_receipt else observed_url},
    ]
    if through_receipt:
        evidence[1]["remote_ref"] = "upload-1"
    if reverse_evidence:
        evidence.reverse()
    assert not verify_readback(
        expected, f"![화면]({observed_url})", expected_assets=approved, observed_assets=evidence,
    )["valid"]


def test_identical_approved_bytes_retain_every_source_url_and_upload_receipt():
    asset_hash = "a" * 64
    source_a, source_b = "https://official.example/one.png", "https://official.example/two.png"
    markdown = f"![첫 화면]({source_a})\n\n![둘째 화면]({source_b})"
    approved = [
        {"hash": asset_hash, "remote_url": source_a, "remote_ref": "upload-a"},
        {"hash": asset_hash, "remote_url": source_b, "remote_ref": "upload-b"},
    ]
    observed = [
        {"hash": asset_hash, "remote_url": source_a},
        {"hash": asset_hash, "remote_url": source_b},
    ]
    assert verify_readback(
        canonicalize_markdown(markdown), markdown, expected_assets=approved, observed_assets=observed,
    )["valid"]
    # Deduplicating content hashes must also retain both persisted upload receipts.
    for receipt in ("upload-a", "upload-b"):
        assert verify_readback(
            canonicalize_markdown(f"![화면](asset://{asset_hash})"), "![화면](https://cdn.example/new)",
            expected_assets=approved,
            observed_assets=[{"remote_ref": receipt, "remote_url": "https://cdn.example/new"}],
        )["valid"]


def test_escaped_pipe_cells_stay_distinct():
    table = canonicalize_markdown("| A | B |\n| --- | --- |\n| x\\|y | z |")
    assert table[0]["rows"] == [[r"x\|y", "z"]]


def test_notion_toggle_structural_tab_preserves_nested_lists_code_and_toggles():
    plain = """<details>
<summary>답안</summary>

- 첫 항목
  - 중첩 항목

```python
if ready:
    print("</details>")
```

<details>
<summary>내부 답안</summary>
중첩 설명
</details>
</details>"""
    enhanced = "\n".join([
        "<details>", "<summary>답안</summary>", "\t", "\t- 첫 항목", "\t  - 중첩 항목",
        "\t", "\t```python", "\tif ready:", '\t    print("</details>")', "\t```",
        "\t", "\t<details>", "\t<summary>내부 답안</summary>", "\t\t중첩 설명",
        "\t</details>", "</details>",
    ])
    expected = canonicalize_markdown(plain)
    assert verify_readback(expected, enhanced)["valid"]
    assert not verify_readback(expected, enhanced.replace("\t  - 중첩 항목", "\t- 중첩 항목"))["valid"]
    assert not verify_readback(expected, enhanced.replace("\t    print", "\tprint"))["valid"]
    assert not verify_readback(expected, enhanced.replace("\t\t중첩 설명", ""))["valid"]


def test_notion_toggle_mixed_structural_indentation_is_not_silently_flattened():
    with pytest.raises(PublicationFormatError, match="structural tab"):
        canonicalize_markdown("<details>\n<summary>답안</summary>\n\t안쪽\n바깥쪽\n</details>")


NOTION_TABLE = '''<table header-row="true">
	<tr><td>개념</td><td>공유 범위</td></tr>
	<tr><td>프로세스</td><td>별도 주소 공간</td></tr>
	<tr><td>스레드</td><td>같은 프로세스</td></tr>
</table>'''


def test_documented_notion_table_matches_unstyled_pipe_table():
    pipe = "| 개념 | 공유 범위 |\n| --- | --- |\n| 프로세스 | 별도 주소 공간 |\n| 스레드 | 같은 프로세스 |"
    expected = canonicalize_markdown(pipe)
    assert verify_readback(expected, NOTION_TABLE)["valid"]
    explicit_defaults = NOTION_TABLE.replace('header-row="true"', 'header-row="true" header-column="false" fit-page-width="false"')
    assert verify_readback(expected, explicit_defaults)["valid"]
    plain_columns = NOTION_TABLE.replace('<tr>', '<colgroup><col><col/></colgroup>\n<tr>', 1)
    assert verify_readback(expected, plain_columns)["valid"]
    empty_columns = NOTION_TABLE.replace('<tr>', '<colgroup></colgroup>\n<tr>', 1)
    assert verify_readback(expected, empty_columns)["valid"]


@pytest.mark.parametrize("changed", [
    NOTION_TABLE.replace('header-row="true"', ''),
    NOTION_TABLE.replace('header-row="true"', 'header-row="false"'),
    NOTION_TABLE.replace('header-row="true"', 'header-row="true" header-column="true"'),
    NOTION_TABLE.replace('header-row="true"', 'header-row="true" fit-page-width="true"'),
    NOTION_TABLE.replace('같은 프로세스', '다른 프로세스'),
    NOTION_TABLE.replace('<td>개념</td><td>공유 범위</td>', '<td>공유 범위</td><td>개념</td>'),
    NOTION_TABLE.replace('<tr><td>프로세스</td><td>별도 주소 공간</td></tr>', ''),
])
def test_notion_table_semantic_changes_never_disappear(changed):
    assert not verify_readback(canonicalize_markdown(NOTION_TABLE), changed)["valid"]


@pytest.mark.parametrize("table", [
    NOTION_TABLE.replace('<td>개념</td>', '<td color="red">개념</td>'),
    NOTION_TABLE.replace('<tr>', '<tr color="blue">', 1),
    NOTION_TABLE.replace('<tr>', '<colgroup><col color="red"><col></colgroup><tr>', 1),
    NOTION_TABLE.replace('<td>개념</td>', '<td><details><summary>nested block</summary></details></td>'),
    NOTION_TABLE.replace('<td>개념</td>', '<td>개념</tr>'),
    NOTION_TABLE.replace('<td>개념</td>', ''),
    NOTION_TABLE.replace('<tr>', '<colgroup><col></colgroup><tr>', 1),
    NOTION_TABLE.replace('header-row="true"', 'header-row="yes"'),
    NOTION_TABLE.replace('header-row="true"', 'header-row="true" header-row="false"'),
])
def test_unsupported_table_styles_blocks_and_malformed_structure_fail_closed(table):
    with pytest.raises(PublicationFormatError):
        canonicalize_markdown(table)


def test_notion_table_rich_text_entities_and_breaks_are_preserved_once():
    pipe = "| A | B |\n| --- | --- |\n| **x\\|y**<br>z | [a & b](https://example.org?a=1&b=2) &lt; |"
    enhanced = '<table header-row="true"><tr><td>A</td><td>B</td></tr><tr><td>**x\\|y**<br/>z</td><td>[a &amp; b](https://example.org?a=1&amp;b=2) &amp;lt;</td></tr></table>'
    assert verify_readback(canonicalize_markdown(pipe), enhanced)["valid"]
