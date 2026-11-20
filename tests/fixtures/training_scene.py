"""Independent literal layout used by public and compiled boundary witnesses."""

def draft():
    return {
        "schema": "lw.scene-draft/1",
        "name": "training-stairs",
        "title": "Training stairs",
        "simulation": {"solver": "mujoco", "warmupSteps": 1, "physicsDt": 0.002},
        "terrain": [
            {"name": "Floor", "shape": "plane"},
            {"name": "Step1", "shape": "box", "position": [1, 0, 0.05], "size": [0.5, 1, 0.1]},
            {"name": "Step2", "shape": "box", "position": [1.5, 0, 0.1], "size": [0.5, 1, 0.2]},
        ],
        "objects": [
            {"name": "Ball", "shape": "sphere", "radius": 0.1, "position": [0.5, 0, 0.1], "mass": 0.25}
        ],
        "sites": [{"name": "Goal", "position": [1.5, 0, 0.2]}],
        "waypoints": {"spawn": {"p": [0, 0, 0], "yaw": 0}},
    }
