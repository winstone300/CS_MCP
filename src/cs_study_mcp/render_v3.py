"""Topic outline rendering using the same evidence/material rendering as v2."""

from .render_v2 import render_readable
from .topic import outline_issues


def render_topic(plan, foundation, advanced, sources, profile, assets):
    report = outline_issues(plan, foundation, advanced)
    if report["errors"]:
        raise ValueError(report["errors"])
    return render_readable(
        plan, foundation, advanced, sources, profile, assets, outline=plan.outline
    )
