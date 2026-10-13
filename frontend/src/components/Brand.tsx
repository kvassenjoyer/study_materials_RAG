import { SERVICE_NAME } from '../branding';
import { Icon } from './Icon';

export function Brand() {
  return <a href="/" className="brand" aria-label={SERVICE_NAME}>
    <span className="brand-icon"><Icon name="book" size={23}/></span>
    <span className="brand-wordmark"><span className="brand-accent">{SERVICE_NAME.slice(0, 3)}</span>{SERVICE_NAME.slice(3)}</span>
  </a>;
}
