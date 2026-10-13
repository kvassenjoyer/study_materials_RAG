export function answerStatusText(status: 'queued' | 'pending') {
  return status === 'queued' ? 'В очереди' : 'Готовлю ответ…';
}

export function AnswerStatus({ status }: { status: 'queued' | 'pending' }) {
  return <span className={`chat-answer-status ${status}`} role="status"><span className={status === 'pending' ? 'spinner' : 'status-dot'}/>{answerStatusText(status)}</span>;
}
