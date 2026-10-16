import asyncio

from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import Command


def test_fixed_step_sequences_are_deterministic_and_reset_preserves_global_sequence():
    async def scenario():
        first, second = SyntheticSource("first"), SyntheticSource("second")
        await first.start("lab-demo", SessionOptions())
        await second.start("lab-demo", SessionOptions())
        try:
            frames = []
            for source in (first, second):
                await source.execute(Command(requestId="pause", command="pause"))
                stepped = await source.execute(Command(requestId="step", command="step", args={"count": 240}))
                assert stepped.frame.simTime == 1.0
                reset = await source.execute(Command(requestId="reset", command="reset"))
                assert reset.frame.seq > stepped.frame.seq and reset.frame.epoch == 1
                assert reset.state.state == "PAUSED"
                repeated = await source.execute(
                    Command(requestId="again", command="step", args={"count": 240})
                )
                assert repeated.frame.poses == stepped.frame.poses
                frames.append(repeated.frame)
            assert frames[0].poses == frames[1].poses
            assert frames[0].simTime == frames[1].simTime == 1.0
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())
