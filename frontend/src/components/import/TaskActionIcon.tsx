import React from "react";

export type TaskActionIconName = "pause" | "resume" | "cancel" | "delete";

const TaskActionIcon: React.FC<{ name: TaskActionIconName }> = ({ name }) => {
  if (name === "pause") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M6.5 5.25v9.5M13.5 5.25v9.5" />
      </svg>
    );
  }

  if (name === "resume") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="m7.25 5 7 5-7 5Z" />
      </svg>
    );
  }

  if (name === "cancel") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="10" cy="10" r="6.75" />
        <path d="m7.5 7.5 5 5m0-5-5 5" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4.75 6.25h10.5M8 6.25V4.5h4v1.75m2 0-.55 9.25h-6.9L6 6.25m2.5 2.5v4.5m3-4.5v4.5" />
    </svg>
  );
};

export default TaskActionIcon;
