import { Icon } from './Icon';

export function AnswerDuration({ milliseconds }: { milliseconds?: number | null }) {
  if (milliseconds == null || !Number.isFinite(milliseconds) || milliseconds < 0) return null;
  const totalSeconds = Math.round(milliseconds / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const duration = minutes ? `${minutes} мин${seconds ? ` ${seconds} с` : ''}` : `${seconds} с`;
  return <span className="answer-duration" title="Время подготовки ответа, включая поиск по материалам и повторные обращения к модели. Ожидание очереди не учитывается.">
    <Icon name="clock" size={13}/>{milliseconds < 1000 ? 'Ответ менее чем за 1 с' : `Ответ за ${duration}`}
  </span>;
}
