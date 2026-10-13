import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../api';
import type { ProjectRecord } from '../types';
import { Icon } from './Icon';
import { ManagementDialog } from './ManagementDialog';

type Action = { kind: 'create' } | { kind: 'rename' | 'delete'; project: ProjectRecord };

export function ProjectSwitcher({ api, project, projects, onSelect, onCreated, onUpdated, onDeleted }: {
  api: ApiClient; project?: ProjectRecord; projects: ProjectRecord[];
  onSelect: (id: string) => void; onCreated: (project: ProjectRecord) => void;
  onUpdated: (project: ProjectRecord) => void; onDeleted: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [action, setAction] = useState<Action | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current!; const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { element.close(); previous?.focus(); };
  }, [open]);
  function edit(next: Action) { setOpen(false); setAction(next); }
  const visible = projects.filter(item => item.title_text.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <>
    {project ? <button className="project-trigger" aria-label={`Выбрать проект: ${project.title_text}`} aria-haspopup="dialog" onClick={() => { setSearch(''); setOpen(true); }}>
      <span className="project-mark"><Icon name="folder" size={22}/></span><span className="project-trigger-text"><span className="project-overline">ТЕКУЩИЙ ПРОЕКТ</span>
        <strong title={project.title_text}>{project.title_text}</strong></span><Icon name="chevron" size={17}/>
    </button> : <button className="primary-button" onClick={() => edit({ kind: 'create' })}><Icon name="plus" size={18}/>Создать проект</button>}
    {open && <dialog ref={dialog} className="project-dialog" aria-labelledby="projects-title" onCancel={event => { event.preventDefault(); setOpen(false); }}
      onClick={event => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div className="project-dialog-heading"><div><h2 id="projects-title">Проекты <span>{projects.length}</span></h2></div>
        <button className="icon-button" aria-label="Закрыть проекты" onClick={() => setOpen(false)}><Icon name="close"/></button></div>
      <p className="project-dialog-description">Свои материалы и чаты для каждой темы</p>
      <label className="project-search"><Icon name="search" size={18}/><input autoFocus aria-label="Найти проект" placeholder="Найти проект" value={search} onChange={event => setSearch(event.target.value)}/></label>
      <div className="project-options">{visible.map(item => <div className={`project-option ${item.project_identifier === project?.project_identifier ? 'active' : ''}`} key={item.project_identifier}>
        <button className="project-option-select" onClick={() => { setOpen(false); onSelect(item.project_identifier); }} aria-current={item.project_identifier === project?.project_identifier ? 'true' : undefined}>
          <span className="project-option-mark">{item.title_text.slice(0, 1).toLocaleUpperCase()}</span><span className="project-option-name">{item.title_text}
            <small>{item.project_identifier === project?.project_identifier ? 'Открыт сейчас' : 'Перейти в проект'}</small></span>
          {item.project_identifier === project?.project_identifier && <Icon name="check" size={17}/>}</button>
        <button className="icon-button" aria-label={`Переименовать проект ${item.title_text}`} onClick={() => edit({ kind: 'rename', project: item })}><Icon name="edit" size={16}/></button>
        <button className="icon-button danger-hover" aria-label={`Удалить проект ${item.title_text}`} onClick={() => edit({ kind: 'delete', project: item })}><Icon name="trash" size={16}/></button>
      </div>)}{!visible.length && <p className="sidebar-empty">Проектов с таким названием нет</p>}</div>
      <div className="project-dialog-footer"><button className="project-create" onClick={() => edit({ kind: 'create' })}><Icon name="plus" size={19}/>Новый проект</button>
        <span><Icon name="folder" size={13}/>Материалы изолированы между проектами</span></div>
    </dialog>}
    {action && <ManagementDialog key={action.kind === 'create' ? 'create' : `${action.kind}-${action.project.project_identifier}`}
      title={action.kind === 'create' ? 'Новый проект' : action.kind === 'rename' ? 'Название проекта' : 'Удалить проект?'}
      initialTitle={action.kind === 'create' ? '' : action.project.title_text} destructive={action.kind === 'delete'}
      description={action.kind === 'delete' ? `«${action.project.title_text}» и все его чаты и материалы будут удалены. Это действие нельзя отменить` : action.kind === 'create' ? 'Соберите чаты и материалы одной темы в отдельном пространстве' : undefined}
      onClose={() => setAction(null)} onConfirm={async (title, signal) => {
        if (action.kind === 'create') { const created = await api.createProject(title, signal); if (!signal.aborted) onCreated(created); }
        else if (action.kind === 'rename') { const updated = await api.renameProject(action.project.project_identifier, title, signal); if (!signal.aborted) onUpdated(updated); }
        else { await api.deleteProject(action.project.project_identifier, signal); if (!signal.aborted) onDeleted(action.project.project_identifier); }
      }}/>}
  </>;
}
