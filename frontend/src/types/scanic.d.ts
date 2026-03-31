declare module "scanic" {
  interface ScannerOptions {
    maxProcessingDimension?: number;
    mode?: "detect" | "extract" | "scan";
    output?: "canvas" | "imagedata" | "dataurl";
  }

  interface ScanResult {
    output: HTMLCanvasElement | ImageData | string;
    corners: any;
    contour: any;
    debug: any;
    success: boolean;
    message: string;
    timings: any[];
  }

  export class Scanner {
    constructor(options?: ScannerOptions);
    initialize(): Promise<void>;
    scan(image: HTMLImageElement | HTMLCanvasElement | ImageData, options?: ScannerOptions): Promise<ScanResult>;
    extract(image: HTMLImageElement | HTMLCanvasElement | ImageData, corners: any, options?: ScannerOptions): Promise<ScanResult>;
  }

  export function initialize(): Promise<void>;
  export function scanDocument(image: any, options?: any): Promise<ScanResult>;
  export function extractDocument(image: any, corners: any, options?: any): Promise<ScanResult>;
}
