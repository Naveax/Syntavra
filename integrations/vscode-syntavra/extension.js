const vscode = require('vscode');
const cp = require('child_process');
function executable(){return vscode.workspace.getConfiguration('syntavra').get('executable','syntavra')}
function run(args, cwd){return new Promise((resolve,reject)=>cp.execFile(executable(),args,{cwd,windowsHide:true,timeout:30000},(e,out,err)=>e?reject(new Error(err||e.message)):resolve(out.trim())))}
function project(){return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd()}
async function activate(context){
  const item=vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left,100); item.command='syntavra.setMode'; item.tooltip='Syntavra optimization status'; item.show(); context.subscriptions.push(item);
  async function refresh(){try{const raw=await run(['--project',project(),'run','statusline'],project()); const data=JSON.parse(raw); item.text=data.statusline||'Syntavra'}catch{item.text='$(warning) Syntavra'}}
  context.subscriptions.push(vscode.commands.registerCommand('syntavra.setMode',async()=>{const mode=await vscode.window.showQuickPick(['full','lite','ultra','commit','review','compress']); if(mode){await run(['--project',project(),'run','mode',mode],project()); refresh()}}));
  context.subscriptions.push(vscode.commands.registerCommand('syntavra.openDashboard',async()=>{const port=String(vscode.workspace.getConfiguration('syntavra').get('dashboardPort',8788)); cp.spawn(executable(),['--project',project(),'run','dashboard','--port',port],{cwd:project(),detached:true,stdio:'ignore',windowsHide:true}).unref(); vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port}/`))}));
  context.subscriptions.push(vscode.commands.registerCommand('syntavra.reindex',()=>run(['--project',project(),'run','watch','--iterations','1'],project())));
  context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(()=>{if(vscode.workspace.getConfiguration('syntavra').get('reindexOnSave',true)) vscode.commands.executeCommand('syntavra.reindex')}));
  const timer=setInterval(refresh,5000); context.subscriptions.push({dispose:()=>clearInterval(timer)}); refresh();
}
function deactivate(){}
module.exports={activate,deactivate};
