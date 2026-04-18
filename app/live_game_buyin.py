"""Derive per-player recorded buy-in for a live game from [LiveGameBuyInEvent] rows only."""

from collections.abc import Iterable

from app.models import LiveGameBuyInEvent


def buy_in_totals_from_events(events: Iterable[LiveGameBuyInEvent]) -> dict[str, int]:
    """Net coins on each player's book: +target, −from on player_transfer; bank_return reduces target."""
    totals: dict[str, int] = {}
    for ev in events:
        tid = ev.target_client_player_id
        fid = ev.from_client_player_id
        if ev.event_kind == "bank_return":
            totals[tid] = totals.get(tid, 0) - ev.coins
        else:
            totals[tid] = totals.get(tid, 0) + ev.coins
            if fid:
                totals[fid] = totals.get(fid, 0) - ev.coins
    return totals
