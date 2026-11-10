"""Prompt files: <name>_<version>.md, shipped in this package (clipper/prompts/) and filled
from [scoring], with the swappable rule files and [scoring] house_style for viewer-facing
prompts. `[paths] prompts` names a folder of your own, read before the packaged files.

VERSIONS is the one list of prompts: which [section] key holds each one's version.
`Prompts(cfg).get(name)` reads that key, so a stage never assigns a version by hand; doctor
checks every file the table names at the configured versions, and tests/test_prompts.py
checks the table against this folder and against every `prompts.get("...")` in the code. A
new prompt is a file here, a config key with its version, and a row in VERSIONS."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from ..errors import ClipperError

VERSIONS: dict[str, tuple[str, str]] = {
    # scoring (the rule files below follow the same version)
    "score_a": ("scoring", "prompt_version"), "score_b": ("scoring", "prompt_version"),
    "scan": ("scoring", "prompt_version"), "summary": ("scoring", "prompt_version"),
    # the pipeline stages
    "segments": ("segments", "prompt_version"), "compilation": ("segments", "prompt_version"),
    "meta": ("meta", "prompt_version"), "chapters": ("meta", "prompt_version"),
    "thumbnail": ("thumbnail", "prompt_version"),
    "thumbnail_enquirer": ("thumbnail", "enquirer_prompt_version"),
    "thumbnail_retry": ("thumbnail", "retry_prompt_version"),
    "thumbnail_enquirer_retry": ("thumbnail", "retry_prompt_version"),
    "thumbnail_feedback": ("thumbnail", "retry_prompt_version"),
    "shorts_layout": ("shorts", "layout_prompt_version"),
    "retake": ("retake", "prompt_version"),
    "pick": ("pick", "prompt_version"), "pick_locate": ("pick", "prompt_version"),
    "preshow": ("preshow", "prompt_version"), "oneoff": ("preshow", "oneoff_prompt_version"),
    # the extras
    "receipts_stories": ("receipts", "prompt_version"),
    "receipts_script": ("receipts", "script_prompt_version"),
    "archive_expand": ("archive", "ask_prompt_version"), "archive_judge": ("archive", "ask_prompt_version"),
    "weekly": ("weekly", "prompt_version"), "weekly_article": ("weekly", "article_prompt_version"),
    "cast_label": ("cast", "prompt_version"),
    "news_judge": ("news", "prompt_version"),
    "investigate_facts": ("investigate", "prompt_version"),
    "investigate_leads": ("investigate", "prompt_version"),
    "investigate_check": ("investigate", "prompt_version"),
    "investigate_confirm": ("investigate", "prompt_version"),
    "swarm_plan": ("swarm", "prompt_version"), "swarm_worker": ("swarm", "prompt_version"),
    "story_triage": ("story", "prompt_version"), "story_expand": ("story", "prompt_version"),
    "story_moments": ("story", "prompt_version"), "story_dossier": ("story", "prompt_version"),
    "story_dig_threads": ("story", "prompt_version"), "story_dig_judge": ("story", "prompt_version"),
    "story_dig_moments": ("story", "prompt_version"), "story_dossier_dig": ("story", "prompt_version"),
    "story_edit": ("story", "edit_prompt_version"),
}
# <kind>_<mode>_<version>.md: rule files spliced into the scoring prompts, at [scoring] prompt_version.
RULE_FILES = ("third_party", "audience", "gambling")

# The files ship with the code: hatchling packs every file under clipper/, so a wheel and the
# hub image carry them. A plain folder here, never a zip.
PACKAGED: Path = Path(str(resources.files(__package__)))


def extra_dir(cfg: dict) -> Path | None:
    """[paths] prompts: a folder of your own prompt files, read before the packaged ones; None
    when unset ("")."""
    d = (cfg.get("paths") or {}).get("prompts")
    return Path(d) if d else None


def find(cfg: dict, filename: str) -> Path:
    """Where `filename` is read from: [paths] prompts when the file is there, else the package's
    copy (which may not exist: the caller says so)."""
    extra = extra_dir(cfg)
    if extra is not None and (extra / filename).exists():
        return extra / filename
    return PACKAGED / filename


def version_of(cfg: dict, name: str) -> str:
    """The configured version of prompt `name`, from the [section] key VERSIONS names."""
    try:
        section, key = VERSIONS[name]
    except KeyError:
        raise ClipperError(f"no prompt named {name!r}: add it to prompts.VERSIONS") from None
    return cfg[section][key]


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


def cast_names(cfg: dict) -> str:
    """{cast}: the show's own people by name, "Andy or PPP" ([meta] cast; "" when unset), so
    prompts never have to say "host" (house style bans it in what viewers see, and the model
    copies it). {cast_note} is the same as an aside, " (Andy or PPP)", or nothing."""
    cast = [c for c in (cfg.get("meta") or {}).get("cast") or [] if c]
    return ", ".join(cast[:-1]) + " or " + cast[-1] if len(cast) > 1 else "".join(cast)


def figures(cfg: dict) -> str:
    """{known_figures}: [scoring] known_figures, plus everyone with a kept photo in the cast
    library ([cast] known_figures, #41) so prompts know the regulars without a hand list."""
    sc = cfg["scoring"]
    if not ((cfg.get("cast") or {}).get("known_figures") and (cfg.get("thumbnail") or {}).get("photos")):
        return sc["known_figures"]
    from ..cast import figures as cast_figures
    from ..cast import known_figures

    return known_figures(sc["known_figures"], cast_figures(cfg))


# Prompts that write text viewers see; [scoring] house_style rules are appended to them.
VIEWER_FACING = {"score_a", "score_b", "meta", "chapters", "segments", "compilation",
                 "thumbnail", "thumbnail_enquirer", "preshow", "oneoff", "pick", "retake",
                 "receipts_stories", "weekly", "weekly_article", "story_edit"}
HOUSE_STYLE = ("House style for anything viewers see (titles, descriptions, hooks, chapter and "
               "thumbnail text):")
# The weekly prompts keep the headers they were written with.
HOUSE_STYLE_HEADERS = {"weekly": "House style for everything on the page:", "weekly_article": "House style:"}


class Prompts:
    """The prompts a config reads, filled with its [scoring] vars (and whatever a stage adds to
    `vars`). `version` is [scoring] prompt_version, the one the rule files follow; every other
    prompt's version comes from VERSIONS through `version_of`."""

    def __init__(self, cfg: dict):
        sc = cfg["scoring"]
        self.cfg = cfg
        self.version = sc["prompt_version"]
        self.extra = extra_dir(cfg)
        self.house_style: list[str] = list(sc.get("house_style") or [])
        self.vars = {"channel_name": sc["channel_name"], "audience": sc["audience"],
                     "tone": sc["tone"], "max_picks": sc["max_picks"],
                     "stream_context": sc["stream_context"],
                     "max_source_audio_s": sc["max_source_audio_s"],
                     "scan_max_per_block": sc["scan_max_per_block"],
                     "known_figures": figures(cfg), "cast": cast_names(cfg)}
        self.vars["cast_note"] = f" ({self.vars['cast']})" if self.vars["cast"] else ""
        # Swappable rule files, <kind>_<mode>_<version>.md, split into "## <section>" parts
        # that fill {<kind>_<section>} placeholders. v3+: third_party; v4+: audience; v7+: gambling.
        for kind, key, modes in (("third_party", "third_party_audio", ("reject", "allow_reactions")),
                                 ("audience", "audience_knowledge", ("strangers", "fans")),
                                 ("gambling", "gambling", ("reject", "commentary"))):
            if sc[key] not in modes:
                raise ClipperError(f"scoring.{key} must be one of {modes}, not {sc[key]!r}")
            rule = find(cfg, f"{kind}_{sc[key]}_{self.version}.md")
            if rule.exists():
                self.vars.update(read_sections(rule, kind, self.vars))
        # v3 prompts name the third-party sections {third_party_rule} / {third_party_rule_b}.
        if "third_party_pass_a" in self.vars:
            self.vars["third_party_rule"] = self.vars["third_party_pass_a"]
            self.vars["third_party_rule_b"] = self.vars["third_party_pass_b"]

    def version_of(self, name: str) -> str:
        return version_of(self.cfg, name)

    def get(self, name: str, **fill) -> str:
        """The prompt at the version its config key sets (VERSIONS). fill: per-call
        placeholders on top of vars (e.g. a reviewer's note)."""
        path = find(self.cfg, f"{name}_{self.version_of(name)}.md")
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            where = f"{PACKAGED}" + (f" or {self.extra}" if self.extra else "")
            raise ClipperError(f"no prompt {path.name} in {where} (clipper doctor lists the missing ones)") from None
        try:
            text = text.format(**{**self.vars, **fill})
        except (KeyError, IndexError, ValueError) as e:
            raise ClipperError(f"{path.name}: placeholder {e} has no value (a literal brace is {{ or }})") from None
        if name in VIEWER_FACING and self.house_style:
            text = (text.rstrip() + "\n\n" + HOUSE_STYLE_HEADERS.get(name, HOUSE_STYLE) + "\n"
                    + "\n".join(f"- {rule}" for rule in self.house_style) + "\n")
        return text
