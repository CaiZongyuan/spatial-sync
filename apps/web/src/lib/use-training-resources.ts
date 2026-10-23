import { useEffect, useState } from 'react';
import { listJobs, listNodes, type TrainingJob, type TrainingNode } from './jobs';

export function useTrainingResources() {
  const [data, setData] = useState<{ jobs?: TrainingJob[]; nodes?: TrainingNode[];
    jobsError?: string; nodesError?: string }>({});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    const read = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      const [jobs, nodes] = await Promise.allSettled([
        listJobs(undefined, controller.signal), listNodes(controller.signal),
      ]);
      if (!disposed) setData(previous => ({
        jobs: jobs.status === 'fulfilled' ? jobs.value : previous.jobs,
        nodes: nodes.status === 'fulfilled' ? nodes.value : previous.nodes,
        jobsError: jobs.status === 'rejected' ? String(jobs.reason?.message ?? jobs.reason) : undefined,
        nodesError: nodes.status === 'rejected' ? String(nodes.reason?.message ?? nodes.reason) : undefined,
      }));
      clearTimeout(timeout); pending = undefined;
    };
    void read();
    const timer = setInterval(() => void read(), 3000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [retry]);
  return { ...data, refresh: () => setRetry(value => value + 1) };
}
