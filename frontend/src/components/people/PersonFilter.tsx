import React from "react";
import { neonColorFromName } from "../../utils/colors";

interface PersonFilterProps {
  persons: string[];
  selected: string[];
  onChange: (persons: string[]) => void;
}

const PersonFilter: React.FC<PersonFilterProps> = ({
  persons,
  selected,
  onChange,
}) => {
  const visiblePersons = Array.from(new Set([...persons, ...selected]));

  const toggle = (p: string) => {
    if (selected.includes(p)) {
      onChange(selected.filter((x) => x !== p));
    } else {
      onChange([...selected, p]);
    }
  };

  return (
    <div style={{ marginBottom: 16 }}>
      {visiblePersons.map((p) => (
        <span
          key={p}
          onClick={() => toggle(p)}
          style={{
            display: "inline-block",
            padding: "6px 12px",
            marginRight: 8,
            marginBottom: 8,
            borderRadius: 4,
            cursor: "pointer",
            background: selected.includes(p)
              ? neonColorFromName(p)
              : "#1f1f22",
          }}
        >
          {p}
        </span>
      ))}
    </div>
  );
};

export default PersonFilter;
