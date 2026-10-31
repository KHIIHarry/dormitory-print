import { CheckCircle2 } from "lucide-react";
import { uploadPhaseLabel } from "../lib/upload-chain";
import type { PrintFile } from "../lib/types";
export function UploadFileStatus({ file }: { file: PrintFile }) {
  return (
    <span
      className={`file-state ${file.status === "FAILED" ? "red-text" : ""}`}
    >
      {file.status === "READY" && <CheckCircle2 size={14} />}
      {uploadPhaseLabel(file)}
    </span>
  );
}

import type { ReactNode } from "react";
import { FileRow } from "../components/ui";
import { Progress } from "../components/feedback";
export function UploadFileRow({
  file,
  action,
}: {
  file: PrintFile;
  action?: ReactNode;
}) {
  const active = file.status === "UPLOADING" || file.status === "PROCESSING";
  const image = !!file.imageSource || file.kind === "PHOTO";
  const indicator = active ? (
    <Progress
      ring={image}
      value={file.uploadPhase === "TRANSFERRING" ? file.progress : undefined}
      label={uploadPhaseLabel(file)}
    />
  ) : undefined;
  return (
    <>
      <FileRow
        file={file}
        action={action}
        indicator={image ? indicator : undefined}
      />
      {!image && indicator}
    </>
  );
}
