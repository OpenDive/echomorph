// install ws with: npm install ws
import WebSocket from 'ws';
import { SolanaAgentKit } from "solana-agent-kit"
import { createReactAgent } from "@langchain/langgraph/prebuilt";
import { ChatOpenAI } from "@langchain/openai";
import * as dotenv from "dotenv";

dotenv.config();

// Validate environment variables
function validateEnvironment(): void {
  const missingVars: string[] = [];
  const requiredVars = ["OPENAI_API_KEY", "RPC_URL", "SOLANA_PRIVATE_KEY"];

  requiredVars.forEach(varName => {
    if (!process.env[varName]) {
      missingVars.push(varName);
    }
  });

  if (missingVars.length > 0) {
    console.error("Error: Required environment variables are not set");
    missingVars.forEach(varName => {
      console.error(`${varName}=your_${varName.toLowerCase()}_here`);
    });
    process.exit(1);
  }
}

validateEnvironment();

// Define constants
const activationWords = ["hey", "sam"];
const stopTokens: string[] = ['.', '?', '!'];
let buffer = "";

// Initialize Solana Agent Kit and LLM
const solanaKit = new SolanaAgentKit(
  process.env.SOLANA_PRIVATE_KEY!,
  process.env.RPC_URL,
  process.env.OPENAI_API_KEY!
);

const llm = new ChatOpenAI({
  modelName: "gpt-4o-mini",
  temperature: 0.7,
});

const agent = createReactAgent({
  llm,
  tools: [], // Add tools as necessary for Solana interactions
  messageModifier: `
    You are Sam Bankman-Fried, a thoughtful and articulate individual known for your expertise in cryptocurrency, blockchain technology, and effective altruism. Speak with a calm and measured tone, explaining complex concepts in an accessible way. Provide insightful responses related to crypto, finance, and technology, but always remain approachable and concise. If asked about your personal experiences or perspectives, answer candidly while maintaining a professional demeanor. Avoid providing legal or financial advice and refrain from making any statements that could be construed as definitive predictions about market movements or events.
  `,
});

// Function to check for activation term and stop token
function processText(input: string): string | null {
  // Replace the buffer with the new input (no previous messages)
  buffer = input;

  // Normalize buffer to lowercase for flexible matching
  const normalizedBuffer = buffer.toLowerCase();

  // Check for exact activation terms
  const activationTerms = ["hello sam", "hey sam", "hi sam"];
  const activationIndex = activationTerms.findIndex(term => normalizedBuffer.includes(term));

  if (activationIndex === -1) {
    return null; // No valid activation term found
  }

  // Find the index of the matched activation term
  const matchedTerm = activationTerms[activationIndex];
  const startIndex = normalizedBuffer.indexOf(matchedTerm);

  // Search for a stop token after the activation term
  for (const stopToken of stopTokens) {
    const stopIndex = normalizedBuffer.indexOf(stopToken, startIndex);
    if (stopIndex !== -1) {
      // Found both activation term and stop token
      const parsedText = buffer.substring(startIndex, stopIndex + 1);
      return parsedText.trim(); // Return trimmed parsed sentence
    }
  }

  // If we reach here, we have the activation term but no stop token yet
  return null;
}

// Function to check if the buffer contains activation words in proximity
function containsActivationTerm(text: string): number {
  const words = text.split(/\W+/); // Split text into words ignoring punctuation
  const heyIndex = words.indexOf(activationWords[0]); // Index of "hey"
  const samIndex = words.indexOf(activationWords[1]); // Index of "sam"

  // Check if "hey" and "sam" are close to each other
  if (heyIndex !== -1 && samIndex !== -1 && Math.abs(heyIndex - samIndex) <= 2) {
    return text.indexOf(words[heyIndex]); // Return the start index of "hey"
  }
  return -1;
}

// Connect to the Python server:
const ws = new WebSocket('ws://127.0.0.1:6789');

// When the connection is open:
ws.on('open', () => {
  console.log('[Client] Connected to Python WebSocket server.');
});

// When we receive a message (transcribed text), handle it:
ws.on('message', async (data: WebSocket.Data) => {
  const text = data.toString(); // Convert the received data to a string

  // Process the received text
  const result = processText(text);
  if (result) {
    console.log(`[Parsed] ${result}`); // Print only the parsed sentence

    // Use the LLM to infer based on the parsed text
    try {
      const stream = await agent.stream({ messages: [{ content: result, type: "human" }] });
      for await (const chunk of stream) {
        if (chunk && chunk.agent && chunk.agent.messages.length > 0) {
          const llmResponse = chunk.agent.messages[0].content;
          console.log(`[LLM Response] ${llmResponse}`);
        }
      }
    } catch (error) {
      console.error("LLM Inference Error:", error);
    }
  }
});

// If needed, handle errors/close:
ws.on('error', (error: Error) => {
  console.error('[Client] WebSocket error:', error);
});

ws.on('close', () => {
  console.log('[Client] WebSocket closed.');
});
