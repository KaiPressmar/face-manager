import React from "react";

interface Props {
  children: React.ReactNode;
  title?: string;
}

interface State {
  error: Error | null;
  retryKey: number;
}

class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, retryKey: 0 };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Oberfläche konnte nicht dargestellt werden:", error, info);
  }

  private retry = () => {
    this.setState((state) => ({ error: null, retryKey: state.retryKey + 1 }));
  };

  render() {
    if (this.state.error) {
      return (
        <section className="page-error" role="alert">
          <span aria-hidden="true">!</span>
          <h2>{this.props.title ?? "Diese Ansicht konnte nicht angezeigt werden"}</h2>
          <p>Deine Daten sind nicht betroffen. Lade die Ansicht erneut; falls das Problem bleibt, wechsle kurz zu einer anderen Seite.</p>
          <button className="neon-button" onClick={this.retry} type="button">Ansicht erneut laden</button>
          <details><summary>Technische Information</summary><code>{this.state.error.message}</code></details>
        </section>
      );
    }
    return <React.Fragment key={this.state.retryKey}>{this.props.children}</React.Fragment>;
  }
}

export default ErrorBoundary;
