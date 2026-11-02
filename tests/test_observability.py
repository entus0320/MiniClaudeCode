import unittest

from miniclaudecode.observability import GenerationWatchConfig, GenerationWatchdog


class GenerationWatchdogTests(unittest.TestCase):
    def test_poll_before_threshold_returns_no_events(self) -> None:
        watchdog = GenerationWatchdog(
            GenerationWatchConfig(idle_timeout_seconds=10, total_timeout_seconds=10),
            phase="plain_chat",
            label="普通对话生成",
            now=100.0,
        )

        self.assertEqual(watchdog.poll(now=105.0), [])

    def test_idle_and_total_timeout_are_reported_once(self) -> None:
        watchdog = GenerationWatchdog(
            GenerationWatchConfig(idle_timeout_seconds=5, total_timeout_seconds=10),
            phase="plain_chat",
            label="普通对话生成",
            now=100.0,
        )

        idle_events = watchdog.poll(now=106.0)
        repeated_idle = watchdog.poll(now=107.0)
        total_events = watchdog.poll(now=111.0)
        repeated_total = watchdog.poll(now=112.0)

        self.assertEqual([event.kind for event in idle_events], ["idle_timeout"])
        self.assertEqual(repeated_idle, [])
        self.assertEqual([event.kind for event in total_events], ["total_timeout"])
        self.assertEqual(repeated_total, [])

    def test_activity_after_timeout_reports_recovered_once(self) -> None:
        watchdog = GenerationWatchdog(
            GenerationWatchConfig(idle_timeout_seconds=5, total_timeout_seconds=100),
            phase="agent_round",
            label="Agent 第 1 轮模型生成",
            round_index=1,
            now=100.0,
        )
        watchdog.poll(now=106.0)

        watchdog.mark_activity(now=107.0)
        recovered = watchdog.poll(now=107.1)
        repeated = watchdog.poll(now=107.2)

        self.assertEqual([event.kind for event in recovered], ["recovered"])
        self.assertEqual(recovered[0].round_index, 1)
        self.assertEqual(repeated, [])

    def test_finish_stops_events(self) -> None:
        watchdog = GenerationWatchdog(
            GenerationWatchConfig(idle_timeout_seconds=1, total_timeout_seconds=1),
            phase="plain_chat",
            label="普通对话生成",
            now=100.0,
        )

        watchdog.finish()

        self.assertEqual(watchdog.poll(now=200.0), [])

    def test_disabled_config_returns_no_events(self) -> None:
        watchdog = GenerationWatchdog(
            GenerationWatchConfig(enabled=False, idle_timeout_seconds=1, total_timeout_seconds=1),
            phase="plain_chat",
            label="普通对话生成",
            now=100.0,
        )

        self.assertEqual(watchdog.poll(now=200.0), [])


if __name__ == "__main__":
    unittest.main()
