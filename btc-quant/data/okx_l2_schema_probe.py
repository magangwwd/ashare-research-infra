"""Inspect a private OKX L2 tar.gz for schema indicators without outputting raw ticks."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile

APPROVED_SHA256 = "829b1393dcf7b9072383092f620039ba15b8be0d7e0fd422e9b66a8afd8b5a7e"
MAX_SAMPLE = 524288


def sha_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as fp:
        while data := fp.read(8 * 1024 * 1024):
            h.update(data)
    return h.hexdigest()


def inspect_archive(path, expected_hash=APPROVED_SHA256):
    actual = sha_file(Path(path))
    if actual != expected_hash:
        raise ValueError("Compressed official L2 file SHA256 mismatch")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile():
            raise ValueError("Unexpected official L2 tar structure")
        member = members[0]
        if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
            raise ValueError("Unsafe tar member path")
        with archive.extractfile(member) as fp:
            sampled = fp.read(MAX_SAMPLE)
    result = {
        "source": "PRIVATE_OKX_L2_2026-10-10_SINGLE_DAY",
        "compressed_sha256": actual,
        "tar_member_count": 1,
        "uncompressed_member_bytes": member.size,
        "sample_limit_bytes": MAX_SAMPLE,
        "sample_bytes_read": len(sampled),
        "sample_sha256": hashlib.sha256(sampled).hexdigest(),
        "raw_market_events_in_report": False,
        "event_completeness": "NOT_VERIFIED_BY_SCHEMA_PROBE",
    }
    try:
        text = sampled.decode("utf-8")
        result["text_encoding"] = "UTF-8"
    except UnicodeDecodeError:
        result["text_encoding"] = "BINARY_OR_OTHER"
        result["format"] = "UNKNOWN"
        return result
    lines = text.splitlines()
    if sampled and sampled[-1] not in b"\r\n":
        lines = lines[:-1]  # ignore incomplete final line
    lines = [line for line in lines if line.strip()]
    result["complete_nonempty_sample_lines"] = len(lines)
    result["max_sample_line_length"] = max((len(x) for x in lines), default=0)
    if not lines:
        result["format"] = "UNKNOWN"
        return result
    parsed = []
    for line in lines[:50]:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            break
    if len(parsed) == min(50, len(lines)) and all(isinstance(x, dict) for x in parsed):
        result["format"] = "JSON_LINES"
        result["json_record_keys"] = sorted(set().union(*(x.keys() for x in parsed)))
        result["json_records_parsed"] = len(parsed)
        return result
    if lines[0].lstrip().startswith(("[", "{")):
        result["format"] = "JSON_OR_OTHER"
        return result
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:min(5, len(lines))]), delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    records = list(csv.reader(lines[:50], delimiter=delimiter))
    widths = [len(row) for row in records]
    result["format"] = "DELIMITED_TEXT"
    result["delimiter"] = {"\t": "TAB", ",": "COMMA", ";": "SEMICOLON", "|": "PIPE"}[delimiter]
    result["field_count_mode"] = max(set(widths), key=widths.count)
    result["widths_unequal"] = len(set(widths)) > 1
    result["header_likely"] = bool(records and
                                   all(c.isidentifier() for c in records[0]) and
                                   all(len(c) < 60 for c in records[0]))
    if result["header_likely"]:
        result["header_names"] = records[0]  # schema labels, not market records
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    report = inspect_archive(Path(a.archive))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
