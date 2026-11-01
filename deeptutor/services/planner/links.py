"""Who may schedule a task: one rule, shared by the router and the tools.

A calendar event can carry ``task_id`` to say "this block is time for that
card". The card must belong to the active workspace, or the block would point
at a task the learner cannot see from where they are standing. The API router
enforces this inline today; tools cannot import a router, so the rule lives here
and the router can adopt it later without changing behaviour.
"""

from __future__ import annotations


def owned_task_ids(workspace_id: str) -> dict[str, str]:
    """``id -> title`` for the cards a workspace may schedule."""
    from deeptutor.services.task_board import get_task_board_store, workspace_board

    board = workspace_board(get_task_board_store().read(), workspace_id)
    return {card.id: card.title for card in board.cards if not card.archived}


def require_owned_task(task_id: str | None, workspace_id: str) -> None:
    """Raises ``ValueError`` when the card is not this workspace's to schedule.

    The message is deliberately the same one a missing card produces: a tool
    must not be a way to learn that a task exists in another workspace.
    """
    if not task_id:
        return
    if task_id not in owned_task_ids(workspace_id):
        raise ValueError("Task not found in this workspace.")


__all__ = ["owned_task_ids", "require_owned_task"]
