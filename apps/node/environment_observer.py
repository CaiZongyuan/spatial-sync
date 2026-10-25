"""Owned success-only environment clock, independent of view IPC and subscriptions."""


class EnvironmentObserver:
    def __init__(self, runner):
        self.runner, self.env = runner, None
        self.originals, self.disabled = {}, False

    def __enter__(self):
        try:
            import torch

            self.torch = torch
            self.env = self.runner.env.unwrapped
            self.selected = list(range(min(self.env.num_envs, 4)))
            if not self.selected:
                raise ValueError("Native observation requires an actual environment")
            self.epochs = [0] * len(self.selected)
            self.dt = self.env.sim.get_physics_dt()
            self.physics_count = self.env.sim.get_physics_step_count()
            self.sim_time = self.physics_count * self.dt
            for name in ("step", "reset"):
                self.originals[name] = (name in vars(self.env), getattr(self.env, name))
                setattr(self.env, name, self._observe(name, getattr(self.env, name)))
        except Exception as error:
            self._observer_error(error)
        return self

    def _observer_error(self, error):
        self.disabled = True
        self.observer_error = str(error)

    def _observe(self, name, original):
        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            if not self.disabled:
                try:
                    if name == "reset":
                        self.epochs = [epoch + 1 for epoch in self.epochs]
                    else:
                        # DirectRLEnv.step returns (obs, rewards, terminated, truncated, extras).
                        done = result[2] | result[3]
                        selected = self.torch.tensor(self.selected, dtype=self.torch.long, device=done.device)
                        resets = done.index_select(0, selected).to(device="cpu").tolist()
                        self.epochs = [
                            epoch + int(reset) for epoch, reset in zip(self.epochs, resets, strict=True)
                        ]
                    count = self.env.sim.get_physics_step_count()
                    if count < self.physics_count:
                        raise ValueError("Actual physics step clock regressed")
                    self.sim_time += (count - self.physics_count) * self.dt
                    self.physics_count = count
                    self._sample()
                except Exception as error:
                    self._observer_error(error)
            return result
        return observed

    def _sample(self):
        pass

    def __exit__(self, *exception):
        if self.env is not None:
            for name, (owned_attribute, original) in self.originals.items():
                if owned_attribute:
                    setattr(self.env, name, original)
                else:
                    delattr(self.env, name)
