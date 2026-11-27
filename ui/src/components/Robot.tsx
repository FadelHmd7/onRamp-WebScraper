// The crawler's mascot: one small robot, drawn in SVG, in the mood of what it
// stands for. Workers walk and work; pages finish (holding a book), wait in
// the dead-letter queue (holding an envelope), retry (dizzy) or fail (tipped
// over). Colours come from CSS variables, so both themes work.

export type Mood = "idle" | "working" | "done" | "waiting" | "retrying" | "dead";

export function Robot({ mood, size = 40 }: { mood: Mood; size?: number }) {
  return (
    <svg
      className={`robot robot-${mood}`}
      viewBox="0 0 40 48"
      width={size}
      height={(size * 48) / 40}
      aria-hidden="true"
      focusable="false"
    >
      <g className="robot-figure">
        {/* antenna */}
        <line x1="20" y1="3" x2="20" y2="9" className="robot-line" />
        <circle cx="20" cy="3" r="2.4" className="robot-light" />
        {/* head */}
        <rect x="7" y="9" width="26" height="16" rx="6" className="robot-body" />
        <Eyes mood={mood} />
        {/* arms */}
        <line x1="10" y1="30" x2="5" y2="36" className="robot-line" />
        <line x1="30" y1="30" x2="35" y2="36" className="robot-line" />
        {/* body */}
        <rect x="11" y="26" width="18" height="13" rx="4" className="robot-body" />
        {/* legs */}
        <rect x="13.5" y="39" width="4.5" height="7" rx="2" className="robot-leg robot-leg-left" />
        <rect x="22" y="39" width="4.5" height="7" rx="2" className="robot-leg robot-leg-right" />
        <Prop mood={mood} />
      </g>
    </svg>
  );
}

function Eyes({ mood }: { mood: Mood }) {
  if (mood === "dead") {
    return (
      <g className="robot-line">
        <path d="M12.5 14.5l4 4M16.5 14.5l-4 4M23.5 14.5l4 4M27.5 14.5l-4 4" />
      </g>
    );
  }
  if (mood === "done") {
    return <path d="M12.5 18.5q2-3 4 0M23.5 18.5q2-3 4 0" className="robot-line" fill="none" />;
  }
  if (mood === "waiting") {
    return <path d="M12.5 17h4M23.5 17h4" className="robot-line" />;
  }
  if (mood === "retrying") {
    return (
      <g className="robot-line" fill="none">
        <circle cx="14.5" cy="17" r="2.4" />
        <circle cx="25.5" cy="17" r="2.4" />
      </g>
    );
  }
  return (
    <g className="robot-eyes">
      <circle cx="14.5" cy="17" r="2.6" />
      <circle cx="25.5" cy="17" r="2.6" />
    </g>
  );
}

function Prop({ mood }: { mood: Mood }) {
  switch (mood) {
    case "done": // a book, carried home
      return (
        <g className="prop-book">
          <rect x="14" y="28.5" width="12" height="8.5" rx="1" />
          <line x1="20" y1="28.5" x2="20" y2="37" className="prop-book-spine" />
        </g>
      );
    case "waiting": // a letter nobody could deliver
      return (
        <g className="prop-letter">
          <rect x="13.5" y="29" width="13" height="8.5" rx="1" />
          <path d="M13.5 29.5l6.5 4.5 6.5-4.5" fill="none" className="prop-letter-flap" />
        </g>
      );
    case "working": // a page in hand
      return <rect x="15" y="29" width="10" height="7" rx="1" className="prop-page" />;
    default:
      return null;
  }
}
