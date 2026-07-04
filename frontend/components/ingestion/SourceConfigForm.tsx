"use client";

import { useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface SourceConfigFormProps {
  sourceType: string;
  onSubmit: (config: Record<string, string>) => void;
}

const FIELD_MAP: Record<string, { label: string; key: string }[]> = {
  mbox: [{ label: INGESTION_COPY.filePathLabel, key: "file_path" }],
  eml: [{ label: INGESTION_COPY.directoryPathLabel, key: "directory_path" }],
  msg: [{ label: INGESTION_COPY.directoryPathLabel, key: "directory_path" }],
  pst: [{ label: INGESTION_COPY.filePathLabel, key: "file_path" }],
  imap: [
    { label: INGESTION_COPY.hostLabel, key: "host" },
    { label: INGESTION_COPY.usernameLabel, key: "username" },
    { label: INGESTION_COPY.passwordLabel, key: "password" },
    { label: INGESTION_COPY.appPasswordEnvLabel, key: "app_password_env" },
  ],
  exchange: [
    { label: INGESTION_COPY.graphUrlLabel, key: "server_url" },
    { label: INGESTION_COPY.usernameLabel, key: "username" },
    { label: INGESTION_COPY.tenantIdLabel, key: "tenant_id" },
    { label: INGESTION_COPY.refreshTokenEnvLabel, key: "refresh_token_env" },
  ],
  gmail: [{ label: INGESTION_COPY.refreshTokenEnvLabel, key: "refresh_token_env" }],
};

export function SourceConfigForm({ sourceType, onSubmit }: SourceConfigFormProps) {
  const fields = FIELD_MAP[sourceType] ?? [];
  const [values, setValues] = useState<Record<string, string>>({});

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">{INGESTION_COPY.configFormHeading}</h3>
      {fields.map((field) => (
        <div key={field.key}>
          <label className="block text-xs text-gray-600">{field.label}</label>
          <input
            type="text"
            value={values[field.key] ?? ""}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
            className="mt-1 w-full rounded border px-3 py-1.5 text-sm"
          />
        </div>
      ))}
      <div>
        <label className="block text-xs text-gray-600">{INGESTION_COPY.segmentLabel}</label>
        <input
          type="text"
          value={values.segment ?? ""}
          onChange={(e) => setValues({ ...values, segment: e.target.value })}
          className="mt-1 w-full rounded border px-3 py-1.5 text-sm"
        />
      </div>
      <button
        onClick={() => onSubmit(values)}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
      >
        Save source
      </button>
    </div>
  );
}
