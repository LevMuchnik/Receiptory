export type Pt = { x: number; y: number };
export type Quad = { topLeft: Pt; topRight: Pt; bottomRight: Pt; bottomLeft: Pt };

export interface DetectionResult {
  corners: Quad | null;
  score: number;
  candidates?: { quad: Quad; score: number }[];
  timingMs: number;
}

export interface Detector {
  readonly name: string;
  detect(image: ImageData, params: any): Promise<DetectionResult>;
  getDefaultParams(): any;
}
