import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

import pytest

from langchain_core.tracers.memory_stream import ClosedResourceError, _MemoryStream


async def test_same_event_loop() -> None:
    """Test that the memory stream works when the same event loop is used.

    This is the easy case.
    """
    reader_loop = asyncio.get_event_loop()
    channel = _MemoryStream[dict](reader_loop)
    writer = channel.get_send_stream()
    reader = channel.get_receive_stream()

    async def producer() -> None:
        """Produce items with slight delay."""
        tic = time.time()
        for i in range(3):
            await asyncio.sleep(0.10)
            toc = time.time()
            await writer.send(
                {
                    "item": i,
                    "produce_time": toc - tic,
                }
            )
        await writer.aclose()

    async def consumer() -> AsyncIterator[dict]:
        tic = time.time()
        async for item in reader:
            toc = time.time()
            yield {
                "receive_time": toc - tic,
                **item,
            }

    asyncio.create_task(producer())

    items = [item async for item in consumer()]

    for item in items:
        delta_time = item["receive_time"] - item["produce_time"]
        # Allow a generous 10ms of delay
        # The test is meant to verify that the producer and consumer are running in
        # parallel despite the fact that the producer is running from another thread.
        # abs_tol is used to allow for some delay in the producer and consumer
        # due to overhead.
        # To verify that the producer and consumer are running in parallel, we
        # expect the delta_time to be smaller than the sleep delay in the producer
        # * # of items = 30 ms
        assert (
            math.isclose(delta_time, 0, abs_tol=0.010) is True
        ), f"delta_time: {delta_time}"


async def test_queue_for_streaming_via_sync_call() -> None:
    """Test via async -> sync -> async path."""
    reader_loop = asyncio.get_event_loop()
    channel = _MemoryStream[dict](reader_loop)
    writer = channel.get_send_stream()
    reader = channel.get_receive_stream()

    async def producer() -> None:
        """Produce items with slight delay."""
        tic = time.time()
        for i in range(3):
            await asyncio.sleep(0.10)
            toc = time.time()
            await writer.send(
                {
                    "item": i,
                    "produce_time": toc - tic,
                }
            )
        await writer.aclose()

    def sync_call() -> None:
        """Blocking sync call."""
        asyncio.run(producer())

    async def consumer() -> AsyncIterator[dict]:
        tic = time.time()
        async for item in reader:
            toc = time.time()
            yield {
                "receive_time": toc - tic,
                **item,
            }

    with ThreadPoolExecutor() as executor:
        executor.submit(sync_call)
        items = [item async for item in consumer()]

    for item in items:
        delta_time = item["receive_time"] - item["produce_time"]
        # Allow a generous 10ms of delay
        # The test is meant to verify that the producer and consumer are running in
        # parallel despite the fact that the producer is running from another thread.
        # abs_tol is used to allow for some delay in the producer and consumer
        # due to overhead.
        # To verify that the producer and consumer are running in parallel, we
        # expect the delta_time to be smaller than the sleep delay in the producer
        # * # of items = 30 ms
        assert (
            math.isclose(delta_time, 0, abs_tol=0.010) is True
        ), f"delta_time: {delta_time}"


async def test_closed_resource_exception() -> None:
    reader_loop = asyncio.get_event_loop()
    channel = _MemoryStream[str](reader_loop)
    writer = channel.get_send_stream()
    reader = channel.get_receive_stream()
    await writer.aclose()
    # OK to use once since resource is available at this point
    # once we start iterating over the reader, we will get the `done` item
    # and mark the stream as closed
    async for _ in reader:
        pass

    # Now stream is closed, so the second time should raise an exception
    with pytest.raises(ClosedResourceError):
        async for _ in reader:
            pass
