/** Serialize autosaves per account/project and provide a read/run barrier. */
export function createWorkspaceWriteQueue(getOwner: () => string) {
  const entries = new Map<string, { tail: Promise<void>; error?: unknown }>();
  function enqueue<T>(projectId: string | null | undefined, write: () => Promise<T>): Promise<T> {
    if (!projectId) return write();
    const owner = getOwner();
    const key = `${owner}:${projectId}`;
    const entry = entries.get(key) || { tail: Promise.resolve() };
    const result = entry.tail.then(() => {
      if (getOwner() !== owner) throw new Error('登录账户已变化，未保存旧账户的配置。');
      return write();
    });
    entry.tail = result.then(() => {}, error => { entry.error = error; });
    entries.set(key, entry);
    const tail = entry.tail;
    void tail.then(() => { if (entries.get(key) === entry && entry.tail === tail && !entry.error) entries.delete(key); });
    return result;
  }
  async function flush(projectId?: string | null) {
    if (!projectId) return;
    const owner = getOwner();
    const key = `${owner}:${projectId}`;
    let entry = entries.get(key);
    while (entry) {
      const tail = entry.tail;
      await tail;
      if (owner !== getOwner()) throw new Error('登录账户已变化，请重新打开项目。');
      if (entry.tail !== tail) { entry = entries.get(key); continue; }
      if (entries.get(key) === entry) entries.delete(key);
      if (entry.error) throw new Error('工作区配置保存失败，请刷新或重新设置后再运行。');
      entry = entries.get(key);
    }
  }
  return { enqueue, flush };
}
