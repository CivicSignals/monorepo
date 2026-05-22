// FoiaAttachmentUpload — file picker + upload trigger for M3 response documents.
//
// Accepts response-document file types: PDF, DOCX, HTML, plain text, EML, MSG.
// Uploads to the FOIA request via the multipart POST endpoint. Shows a spinner
// during upload, a success message on completion, and an error alert on failure.

"use client";

import { useRef, useState } from "react";
import { useUploadFoiaAttachment } from "@/hooks/use-foia";
import { ProblemError } from "@/lib/auth-api";

interface FoiaAttachmentUploadProps {
  requestId: string;
}

export function FoiaAttachmentUpload({ requestId }: FoiaAttachmentUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const { mutate: upload, isPending, error, reset } = useUploadFoiaAttachment(requestId);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    setSuccessMsg(null);
    reset();
  }

  function handleUpload() {
    if (!selectedFile) return;
    upload(selectedFile, {
      onSuccess: (att) => {
        setSuccessMsg(`"${att.filename}" uploaded — extraction queued.`);
        setSelectedFile(null);
        if (inputRef.current) inputRef.current.value = "";
      },
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="foia-response-file">
          Choose response file
        </label>
        <input
          ref={inputRef}
          id="foia-response-file"
          type="file"
          accept=".pdf,.html,.docx,.txt,.eml,.msg"
          aria-label="Choose response file"
          className="text-sm text-muted-foreground file:mr-3 file:rounded file:border file:border-input file:bg-background file:px-3 file:py-1 file:text-sm file:font-medium file:text-foreground"
          onChange={handleFileChange}
          disabled={isPending}
        />
        <button
          type="button"
          onClick={handleUpload}
          disabled={!selectedFile || isPending}
          aria-busy={isPending}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isPending ? "Uploading…" : "Upload response"}
        </button>
      </div>

      {successMsg && (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          {successMsg}
        </p>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error instanceof ProblemError
            ? (error.problem.detail ?? error.problem.title)
            : error.message}
        </p>
      )}
    </div>
  );
}
