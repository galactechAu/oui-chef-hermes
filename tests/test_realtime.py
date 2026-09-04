import unittest


class EventHubTests(unittest.TestCase):
    def test_publish_delivers_monotonic_event_to_subscriber(self):
        from realtime import EventHub
        hub = EventHub()
        subscriber = hub.subscribe()
        first = hub.publish("list.changed", "list-a")
        second = hub.publish("import_job.changed", "job-a")
        self.assertLess(first["id"], second["id"])
        self.assertEqual(subscriber.get(timeout=0.1)["resource_id"], "list-a")
        self.assertEqual(subscriber.get(timeout=0.1)["type"], "import_job.changed")

    def test_unsubscribed_client_receives_no_future_events(self):
        from realtime import EventHub
        hub = EventHub()
        subscriber = hub.subscribe()
        hub.unsubscribe(subscriber)
        hub.publish("lists.changed")
        self.assertTrue(subscriber.empty())


if __name__ == "__main__":
    unittest.main()
