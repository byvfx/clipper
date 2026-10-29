import argparse

from clipper import cli, stages


def commands() -> dict[str, argparse.ArgumentParser]:
    sub = argparse.ArgumentParser().add_subparsers()
    for add in (cli.add_pipeline, cli.add_review, cli.add_services, cli.add_extras, cli.add_setup):
        add(sub)
    return sub.choices


def test_every_command_is_in_one_group():
    """`clipper -h` lists commands by COMMAND_GROUPS: a new command goes in one."""
    grouped = [n for _, names in cli.COMMAND_GROUPS for n in names]
    assert sorted(grouped) == sorted(commands())
    assert len(grouped) == len(set(grouped))


def test_every_stage_runs_a_real_command():
    cmds = commands()
    wc = {"fast_lane": True, "notify": False}
    for st in stages.STAGES:
        argv = stages.argv(st.name, "kick-1", wc)
        parser = cmds[argv[0]]
        parser.parse_args(argv[1:])   # the worker's argv parses (exits on an unknown flag)
