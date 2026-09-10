/** Neutral splash while the backend is still coming up (fe.6). */
import { S } from "../strings";
export function Starting() {
  return (
    <div className="screen">
      <h1>{S.app.name}</h1>
      <p>{S.app.starting}</p>
    </div>
  );
}
