import textwrap

from django.core.management.base import BaseCommand


def command_docstring(cmd: type[BaseCommand]) -> str:
    # Collect the *base* (inherited) options so command-specific options can be
    # listed first. This must come from BaseCommand, not `cmd` itself — using
    # `cmd()` would put every option (including the command's own) into base_args
    # and scatter the command's flags among the inherited ones.
    base_args: set[str] = set()
    if cmd is not BaseCommand:  # pragma: no branch
        base_parser = BaseCommand().create_parser("base", "")
        for group in base_parser._action_groups:
            base_args.update(
                ",".join(action.option_strings) for action in group._group_actions
            )
    parser = cmd().create_parser("command", "")
    doc = parser.description or ""

    if cmd.__doc__:  # pragma: no branch
        if doc:  # pragma: no branch
            doc += "\n\n"
        doc += textwrap.dedent(cmd.__doc__)
    args = []
    for group in parser._action_groups:
        for action in group._group_actions:
            if "--help" in action.option_strings:
                continue
            # Keep the bare option name for base/command grouping; the type
            # suffix is display-only and must not affect the comparison.
            name = ",".join(action.option_strings)
            display_name = name
            action_type = action.type
            if not action_type and action.nargs != 0:
                action_type = str
            if action_type:
                if isinstance(action_type, type):  # pragma: no branch
                    action_type = action_type.__name__
                display_name += f" ({action_type})"
            help = action.help or ""
            if help and not action.required and action.nargs != 0:
                if not help.endswith("."):
                    help += "."
                if action.default is not None:
                    help += f" Defaults to {action.default}."
                else:
                    help += " Optional."
            args.append((name, display_name, help))
    # Sort args from this class first, then base args (compare on bare name).
    args.sort(key=lambda o: (o[0] in base_args, o[0]))
    if args:  # pragma: no branch
        doc += "\n\nAttributes:"
        for _name, display_name, description in args:
            doc += f"\n    {display_name}: {description}"
    return doc
