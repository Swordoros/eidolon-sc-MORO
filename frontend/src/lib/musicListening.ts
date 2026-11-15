// What the producer is listening to, for the chat prompt. The player sets it while a
// song is actually playing; a paused or stopped player leaves nothing here.

let listening: string | null = null;

export function setListening(value: string | null): void {
  listening = value;
}

export function getListening(): string | null {
  return listening;
}
