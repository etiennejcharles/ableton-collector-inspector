from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from textual.widgets import Button, Static

from ableton_audio_collector.batch import build_review_rows
from ableton_audio_collector.collector import CollectionApplyError, CollectionFailure
from ableton_audio_collector.tui import CollectionScreen, ConfirmScreen, ReviewApp, ReviewFilter

from .conftest import mixed_plugin_als, touch_audio, write_als


def test_review_app_can_be_created(tmp_path):
    app = ReviewApp(tmp_path)

    assert app.folder == tmp_path


def test_review_details_show_missing_audio_paths(tmp_path):
    set_file = write_als(
        tmp_path / "Project" / "Song.als",
        """
        <Ableton>
          <FileRef>
            <RelativePath Value="Samples/Missing/ghost.wav" />
            <Path Value="/Volumes/Samples/ghost.wav" />
          </FileRef>
        </Ableton>
        """,
    )
    row = build_review_rows(tmp_path)[0]
    app = ReviewApp(tmp_path)

    details = app._render_details(row)

    assert row.set_file == set_file.resolve()
    assert "Missing audio paths:" in details
    assert "ref #1: Samples/Missing/ghost.wav" in details
    assert "stored path: /Volumes/Samples/ghost.wav" in details


def test_review_details_show_plugins_grouped_by_track(tmp_path):
    write_als(tmp_path / "Project" / "Plugins.als", mixed_plugin_als())
    row = build_review_rows(tmp_path)[0]

    details = ReviewApp(tmp_path)._render_details(row)

    assert "PLUG-INS BY TRACK" in details
    assert "Vocals [audio]" in details
    assert "AU     Auto-Tune Pro" in details
    assert "VST3   Pro-Q 3" in details
    assert "Synth [midi]" in details
    assert "VST2   Serum" in details


def test_dashboard_counts_filters_and_selection(tmp_path):
    _build_dashboard_folder(tmp_path)

    async def scenario():
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)

            assert _plain(app.query_one("#metric-total", Static)) == "4\nTOTAL SETS"
            assert _plain(app.query_one("#metric-ready", Static)) == "1\nREADY"
            assert _plain(app.query_one("#metric-safe", Static)) == "1\nSAFE"
            assert _plain(app.query_one("#metric-issues", Static)) == "2\nISSUES"
            assert app._table().row_count == 4

            await pilot.click("#filter-ready")
            await pilot.pause()
            assert app.active_filter == ReviewFilter.READY
            assert app._table().row_count == 1

            await pilot.click("#sets", offset=(3, 1))
            await pilot.pause()
            assert len(app.selected_ids) == 1
            assert app.query_one("#collect-button", Button).disabled is False

            await pilot.click("#filter-safe")
            await pilot.pause()
            assert app.active_filter == ReviewFilter.SAFE
            assert app._table().row_count == 1
            assert len(app.selected_ids) == 1

            await pilot.press("enter")
            await pilot.pause()
            assert len(app.selected_ids) == 1
            assert "Cannot select this Set" in _plain(app.query_one("#details", Static))

            await pilot.click("#filter-all")
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert len(app.selected_ids) == 1

            await pilot.press("escape")
            await pilot.pause()
            assert app.selected_ids == set()
            assert app.query_one("#collect-button", Button).disabled is True

    asyncio.run(scenario())


def test_filter_cycle_preserves_selection_and_clear_button_resets_it(tmp_path):
    _build_ready_sets(tmp_path, count=2)

    async def scenario():
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)

            await pilot.press("ctrl+a")
            await pilot.pause()
            assert len(app.selected_ids) == 2

            await pilot.press("f")
            await pilot.pause()
            assert app.active_filter == ReviewFilter.READY
            assert len(app.selected_ids) == 2

            await pilot.click("#clear-selection")
            await pilot.pause()
            assert app.selected_ids == set()

    asyncio.run(scenario())


def test_empty_and_failed_scan_states_offer_retry(tmp_path, monkeypatch):
    import ableton_audio_collector.tui as tui_module

    async def scenario():
        def fail_scan(_folder):
            raise RuntimeError("external volume is offline")

        monkeypatch.setattr(tui_module, "build_review_rows", fail_scan)
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)

            assert "SCAN FAILED" in _plain(app.query_one("#scan-state", Static))
            assert "external volume is offline" in _plain(app.query_one("#list-state-copy", Static))
            assert app.query_one("#retry-scan", Button).display is True

            monkeypatch.setattr(tui_module, "build_review_rows", lambda _folder: [])
            await pilot.click("#retry-scan")
            await _wait_for_scan(app, pilot)

            assert "SCAN COMPLETE" in _plain(app.query_one("#scan-state", Static))
            assert "No Ableton Sets found" in _plain(app.query_one("#list-state-copy", Static))

    asyncio.run(scenario())


def test_confirmation_cancel_does_not_apply_collection(tmp_path, monkeypatch):
    import ableton_audio_collector.tui as tui_module

    _build_ready_sets(tmp_path, count=1)
    calls = []
    monkeypatch.setattr(tui_module, "apply_collection", lambda *args, **kwargs: calls.append(args))

    async def scenario():
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)
            await pilot.press("space")
            await pilot.press("a")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#cancel")
            await pilot.pause()

            assert calls == []
            assert len(app.selected_ids) == 1

    asyncio.run(scenario())


def test_collection_screen_runs_all_sets_and_refreshes(tmp_path, monkeypatch):
    import ableton_audio_collector.tui as tui_module

    _build_ready_sets(tmp_path, count=2)
    calls = []

    def succeed(set_file, selected_ids, project_root):
        calls.append((set_file, selected_ids, project_root))
        return SimpleNamespace(
            backup_path=project_root / "Backup" / set_file.name,
            copied_audio=[(Path("source.wav"), Path("destination.wav"))],
            copied_asd=[],
            rewritten_reference_count=1,
            final_report=SimpleNamespace(
                external_collectable_count=0,
                missing_count=0,
            ),
        )

    monkeypatch.setattr(tui_module, "apply_collection", succeed)

    async def scenario():
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)
            await pilot.press("ctrl+a")
            await pilot.press("a")
            await pilot.pause()
            await pilot.click("#apply")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, CollectionScreen)
            assert screen._apply_task is not None
            await screen._apply_task
            await pilot.pause()

            assert len(calls) == 2
            assert len(screen.outcomes) == 2
            assert all(outcome.succeeded for outcome in screen.outcomes)
            assert len(screen.query(".outcome-card")) == 2
            assert "SUCCESS" in _plain(screen.query_one(".outcome-card", Static))
            assert screen.query_one("#collection-done", Button).disabled is False

            await pilot.click("#collection-done")
            await pilot.pause()
            await _wait_for_scan(app, pilot)
            assert app.selected_ids == set()

    asyncio.run(scenario())


def test_collection_screen_stops_on_failure_and_shows_rollback(tmp_path, monkeypatch):
    import ableton_audio_collector.tui as tui_module

    _build_ready_sets(tmp_path, count=2)
    calls = []

    def fail(set_file, selected_ids, project_root):
        calls.append(set_file)
        raise CollectionApplyError(
            CollectionFailure(
                message="destination is unavailable",
                failed_step="copy",
                backup_path=project_root / "Backup" / set_file.name,
                rollback_attempted=True,
                rollback_succeeded=True,
            )
        )

    monkeypatch.setattr(tui_module, "apply_collection", fail)

    async def scenario():
        app = ReviewApp(tmp_path)
        async with app.run_test(size=(120, 35)) as pilot:
            await _wait_for_scan(app, pilot)
            await pilot.press("ctrl+a")
            await pilot.press("a")
            await pilot.pause()
            await pilot.click("#apply")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, CollectionScreen)
            assert screen._apply_task is not None
            await screen._apply_task
            await pilot.pause()

            outcome_text = _plain(screen.query_one(".outcome-card", Static))
            assert len(calls) == 1
            assert len(screen.outcomes) == 1
            assert screen.outcomes[0].succeeded is False
            assert "FAILED" in outcome_text
            assert "Rollback: succeeded" in outcome_text
            assert "Collection stopped" in _plain(screen.query_one("#collection-title", Static))

    asyncio.run(scenario())


async def _wait_for_scan(app: ReviewApp, pilot) -> None:
    assert app._load_task is not None
    await app._load_task
    await pilot.pause()


def _plain(widget: Static) -> str:
    rendered = widget.render()
    return getattr(rendered, "plain", str(rendered))


def _build_dashboard_folder(tmp_path) -> None:
    _build_ready_sets(tmp_path, count=1)

    safe_project = tmp_path / "Safe Project"
    touch_audio(safe_project / "Samples" / "Imported" / "local.wav")
    write_als(
        safe_project / "Safe Project.als",
        '<Ableton><FileRef><RelativePath Value="Samples/Imported/local.wav" /></FileRef></Ableton>',
    )

    write_als(
        tmp_path / "Missing Project" / "Missing Project.als",
        '<Ableton><FileRef><Path Value="/Volumes/Offline/missing.wav" /></FileRef></Ableton>',
    )
    write_als(tmp_path / "Unreadable Project" / "Unreadable.als", "<Ableton>")


def _build_ready_sets(tmp_path, count: int) -> None:
    for index in range(1, count + 1):
        external = touch_audio(tmp_path / "External" / f"loop-{index}.wav")
        write_als(
            tmp_path / f"Ready Project {index}" / f"Ready {index}.als",
            f'<Ableton><FileRef><Path Value="{external}" /></FileRef></Ableton>',
        )
