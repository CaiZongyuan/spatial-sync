"""Cost browser clock boundary on the existing GPU-free authenticated node fixture."""

import math
import time

from fastapi import HTTPException
from training_resources_gateway import app as app

observed = [time.time()]
app.state.jobs.clock = lambda: observed[0]


@app.post("/e2e/cost-time")
async def cost_time(body: dict):
    value = body.get("at")
    if type(value) not in (int, float) or not math.isfinite(value):
        raise HTTPException(422)
    observed[0] = value
    return {"at": value}
