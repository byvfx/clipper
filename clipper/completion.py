"""`clipper completion bash|powershell` (#205): a tab-completion script for the shell, made
from the argument parser itself, so it knows every command, action and flag of this
version. Stream arguments complete to the shortcuts `latest` and `latest:<show>` (the shows
of this config); ids themselves are left to the shell's own path completion, since listing
the streams folder on the share at every Tab would be slow. Write the script to a file
once and load it from the shell's profile; make it again after an update that adds commands."""

from __future__ import annotations

import argparse

from .errors import ClipperError

SHELLS = ("bash", "powershell")
GLOBAL_VALUE_OPTS = ("--config", "--show")  # top-level options that take a value


def _subparsers(p: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            return dict(a.choices)
    return {}


def _words(p: argparse.ArgumentParser, streams: list[str]) -> list[str]:
    """What can follow this (sub)command: its actions, its flags, and the stream shortcuts
    when it takes a stream."""
    out = list(_subparsers(p))
    for a in p._actions:
        if a.help == argparse.SUPPRESS or isinstance(a, argparse._SubParsersAction):
            continue
        out += [o for o in a.option_strings if o.startswith("--")]
        if not a.option_strings and a.dest == "stream":
            out += streams
    return list(dict.fromkeys(out))


def table(parser: argparse.ArgumentParser, shows: list[str]) -> dict[str, list[str]]:
    """{"": top-level words, "<command>": its words, "<command> <action>": the action's}."""
    streams = ["latest"] + [f"latest:{s}" for s in shows]
    out = {"": _words(parser, streams)}
    for name, p in _subparsers(parser).items():
        out[name] = _words(p, streams)
        for action, q in _subparsers(p).items():
            out[f"{name} {action}"] = _words(q, streams)
    return out


def bash(t: dict[str, list[str]]) -> str:
    cases = "\n".join(f'        "{k}") echo "{" ".join(v)}" ;;' for k, v in t.items() if k)
    skip = "|".join(GLOBAL_VALUE_OPTS)
    return f"""# clipper tab completion for bash: made by `clipper completion bash`
_clipper_words() {{
    case "$1" in
{cases}
        "") echo "{" ".join(t[""])}" ;;
        *) return 1 ;;
    esac
}}
_clipper() {{
    # the word at the cursor from the line itself: bash splits latest:kino at the colon
    local cur="${{COMP_LINE:0:COMP_POINT}}" i w=() key=""
    cur="${{cur##* }}"
    for ((i = 1; i < COMP_CWORD; i++)); do
        case "${{COMP_WORDS[i]}}" in
            {skip}) ((i++)) ;;
            -*) ;;
            *) w+=("${{COMP_WORDS[i]}}") ;;
        esac
    done
    if ((${{#w[@]}} >= 2)) && _clipper_words "${{w[0]}} ${{w[1]}}" >/dev/null; then key="${{w[0]}} ${{w[1]}}"
    elif ((${{#w[@]}} >= 1)) && _clipper_words "${{w[0]}}" >/dev/null; then key="${{w[0]}}"
    fi
    COMPREPLY=($(compgen -W "$(_clipper_words "$key")" -- "$cur"))
    if [[ $cur == *:* ]]; then
        local done="${{cur%:*}}:"
        COMPREPLY=("${{COMPREPLY[@]#"$done"}}")
    fi
}}
complete -o default -F _clipper clipper clipper.exe
"""


def powershell(t: dict[str, list[str]]) -> str:
    rows = "\n".join(f"        '{k}' = '{' '.join(v)}'" for k, v in t.items())
    skip = ", ".join(f"'{o}'" for o in GLOBAL_VALUE_OPTS)
    return f"""# clipper tab completion for PowerShell: made by `clipper completion powershell`
Register-ArgumentCompleter -Native -CommandName clipper, clipper.exe -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $table = @{{
{rows}
    }}
    $words = @()
    $skipNext = $false
    $elements = @($commandAst.CommandElements | Select-Object -Skip 1)
    foreach ($e in $elements) {{
        if ($e.Extent.StartOffset -ge $cursorPosition -or ($wordToComplete -and $e.Extent.EndOffset -eq $cursorPosition)) {{ break }}
        $s = $e.ToString()
        if ($skipNext) {{ $skipNext = $false; continue }}
        if (@({skip}) -contains $s) {{ $skipNext = $true; continue }}
        if (-not $s.StartsWith('-')) {{ $words += $s }}
    }}
    $key = ''
    if ($words.Count -ge 2 -and $table.ContainsKey("$($words[0]) $($words[1])")) {{ $key = "$($words[0]) $($words[1])" }}
    elseif ($words.Count -ge 1 -and $table.ContainsKey($words[0])) {{ $key = $words[0] }}
    $table[$key] -split ' ' | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }}
}}
"""


def script(shell: str, parser: argparse.ArgumentParser, shows: list[str]) -> str:
    if shell not in SHELLS:
        raise ClipperError(f"completion: {shell!r} isn't one of {', '.join(SHELLS)}")
    t = table(parser, shows)
    return bash(t) if shell == "bash" else powershell(t)
