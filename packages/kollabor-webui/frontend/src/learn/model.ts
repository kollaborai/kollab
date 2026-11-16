export type LearnCommand = {
  syntax: string;
  example: string;
  description: string;
  expect?: string;
};

export type LearnStep = {
  action: string;
  expect?: string;
};

export type LearnMedia = {
  kind: "image" | "video";
  src: string;
  alt: string;
  poster?: string;
  captions?: string;
  caption?: string;
};

export type LearnWorkflow = {
  id: string;
  label: string;
  summary?: string;
  entry: {
    prerequisite?: string;
    ui?: string[];
    command?: string;
  };
  steps: LearnStep[];
  commands?: LearnCommand[];
  media?: LearnMedia;
  success: string;
  recovery: { symptom: string; action: string }[];
  note?: string;
};

export type LearnTopicDefinition = {
  id: string;
  title: string;
  summary: string;
  workflows: LearnWorkflow[];
  note?: string;
  related?: string[];
};
