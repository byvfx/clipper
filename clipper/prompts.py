"""Prompt files: prompts/<name>_<version>.md, filled from [scoring], with the swappable
rule files and [scoring] house_style for viewer-facing prompts."""
from __future__ import annotations

from pathlib import Path

from .errors import ClipperError


def read_sections(path: Path, prefix: str, fmt: dict) -> dict[str, str]:
    """Split a rule file on "## name" lines into {prefix_name: text}."""
    out, name, buf = {}, None, []
    for line in path.read_text(encoding="utf-8").format(**fmt).splitlines():
        if line.startswith("## "):
            if name:
                out[f"{prefix}_{name}"] = "\n".join(buf).strip()
            name, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if name:
        out[f"{prefix}_{name}"] = "\n".join(buf).strip()
    return out


# Prompts that write text viewers see; [scoring] house_style rules are appended to them.
VIEWER_FACING = {"score_a", "score_b", "meta", "chapters", "segments", "compilation",
                 "thumbnail", "thumbnail_enquirer", "preshow", "oneoff", "pick", "retake",
                 "receipts_stories", "weekly", "weekly_article"}
HOUSE_STYLE = ("House style for anything viewers see (titles, descriptions, hooks, chapter and "
               "thumbnail text):")
# The weekly prompts keep the headers they were written with.
HOUSE_STYLE_HEADERS = {"weekly": "House style for everything on the page:", "weekly_article": "House style:"}


class Prompts:
    def __init__(self, cfg: dict):
        sc = cfg["scoring"]
        self.version = sc["prompt_version"]
        self.dir: Path = cfg["paths"]["prompts"]
        self.house_style: list[str] = list(sc.get("house_style") or [])
        self.vars = {"channel_name": sc["channel_name"], "audience": sc["audience"],
                     "tone": sc["tone"], "max_picks": sc["max_picks"],
                     "stream_context": sc["stream_context"],
                     "max_source_audio_s": sc["max_source_audio_s"],
                     "scan_max_per_block": sc["scan_max_per_block"],
                     "known_figures": sc["known_figures"]}
        # Swappable rule files, <kind>_<mode>_<version>.md, split into "## <section>" parts
        # that fill {<kind>_<section>} placeholders. v3+: third_party; v4+: audience.
        for kind, key, modes in (("third_party", "third_party_audio", ("reject", "allow_reactions")),
                                 ("audience", "audience_knowledge", ("strangers", "fans"))):
            if sc[key] not in modes:
                raise ClipperError(f"scoring.{key} must be one of {modes}, not {sc[key]!r}")
            rule = self.dir / f"{kind}_{sc[key]}_{self.version}.md"
            if rule.exists():
                self.vars.update(read_sections(rule, kind, self.vars))
        # v3 prompts name the third-party sections {third_party_rule} / {third_party_rule_b}.
        if "third_party_pass_a" in self.vars:
            self.vars["third_party_rule"] = self.vars["third_party_pass_a"]
            self.vars["third_party_rule_b"] = self.vars["third_party_pass_b"]

    def get(self, name: str) -> str:
        text = (self.dir / f"{name}_{self.version}.md").read_text(encoding="utf-8")
        text = text.format(**self.vars)
        if name in VIEWER_FACING and self.house_style:
            text = (text.rstrip() + "\n\n" + HOUSE_STYLE_HEADERS.get(name, HOUSE_STYLE) + "\n"
                    + "\n".join(f"- {rule}" for rule in self.house_style) + "\n")
        return text
