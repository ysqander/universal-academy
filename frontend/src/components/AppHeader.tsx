import { Link } from "react-router-dom";
import { getHealth } from "../api";
import { useResource } from "../hooks/useResource";
import "./AppHeader.css";

function Wordmark() {
  return (
    <Link to="/" className="wordmark" aria-label="Universal Academy — library">
      <svg className="wordmark__mark" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="8" />
        <path d="M16 9.5 10 21.5M16 9.5l6 12M10 21.5h12" />
        <circle cx="16" cy="9" r="3.2" />
        <circle cx="10" cy="22" r="3.2" />
        <circle cx="22" cy="22" r="3.2" className="wordmark__due" />
      </svg>
      <span className="wordmark__text">
        Universal <em>Academy</em>
      </span>
    </Link>
  );
}

export function AppHeader() {
  const health = useResource("health", getHealth);
  const fake = health.data?.fake_llm;
  return (
    <header className="app-header">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <div className="app-header__inner">
        <Wordmark />
        <nav className="app-header__nav" aria-label="Main">
          {fake && (
            <span className="app-header__mode" title="UA_FAKE_LLM=1: courses are built from canned offline content">
              Offline demo mode
            </span>
          )}
          <Link to="/" className="app-header__link">
            Library
          </Link>
        </nav>
      </div>
    </header>
  );
}
