import React from "react";
import { neonColorFromName } from "../../utils/colors";

const PersonFilter = ({ images, selected, onChange }) => {
  const persons = new Set<string>();

  images.forEach((img) =>
    img.faces.forEach((f) => persons.add(f.person_name || "Unbekannt"))
  );

  const toggle = (p: string) => {
    if (selected.includes(p)) {
      onChange(selected.filter((x) => x !== p));
    } else {
      onChange([...selected, p]);
    }
  };

  return (
    <div style={{ marginBottom: 16 }}>
      {[...persons].map((p) => (
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
