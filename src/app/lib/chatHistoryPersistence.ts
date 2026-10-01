import { getCurrentUserId, saveAgentHistory } from "./api";
import type { AgentChatMessage } from "./api";

// Serialize writes per owner/scope: a slow old snapshot cannot overwrite a
// newer one. Returning to a scope waits for its pending writes before reading.
const pending = new Map<string, Promise<unknown>>();

export function persistChatHistory(owner: string, projectId: string, messages: AgentChatMessage[]) {
  const key = `${owner}:${projectId}`;
  const previous = pending.get(key) || Promise.resolve();
  const task = previous.catch(() => {}).then(() => {
    if (getCurrentUserId() !== owner) return;
    return saveAgentHistory(projectId, messages);
  });
  pending.set(key, task);
  const clean = () => { if (pending.get(key) === task) pending.delete(key); };
  void task.then(clean, clean);
  return task;
}

export async function waitForChatHistory(owner: string, projectId: string) {
  await pending.get(`${owner}:${projectId}`)?.catch(() => {});
}
