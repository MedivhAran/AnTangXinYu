import { ReadableStream } from 'stream/web';
import { TextDecoder, TextEncoder } from 'util';

Object.defineProperty(globalThis, 'ReadableStream', { value: ReadableStream });
Object.defineProperty(globalThis, 'TextDecoder', { value: TextDecoder });
Object.defineProperty(globalThis, 'TextEncoder', { value: TextEncoder });
