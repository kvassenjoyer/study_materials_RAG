/** Invalidates results synchronously when the selected chat or account changes. */
export class RequestScope {
  private alive = true;
  private generation = 0;
  private chatId: string | null = null;

  select(chatId: string | null): void { this.chatId = chatId; this.generation += 1; }
  session(): () => boolean { return () => this.alive; }
  selection(): () => boolean {
    const generation = this.generation;
    const chatId = this.chatId;
    return () => this.alive && this.generation === generation && this.chatId === chatId;
  }
  dispose(): void { this.alive = false; this.generation += 1; }
}
