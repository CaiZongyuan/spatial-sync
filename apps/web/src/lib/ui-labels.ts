const sessions: Record<string, string> = {
  CREATED: '已创建', STARTING: '启动中', RUNNING: '运行中', PAUSED: '已暂停',
  STOPPING: '正在停止', STOPPED: '已停止', FAILED: '失败',
};
const connections: Record<string, string> = { connecting: '连接中', connected: '已连接', disconnected: '已断开' };

export const sessionLabel = (state: string) => sessions[state] ?? state;
export const connectionLabel = (state: string) => connections[state] ?? state;
