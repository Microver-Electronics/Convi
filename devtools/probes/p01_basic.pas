procedure Body;
var
  Doc, Board, T: Variant;
begin
  L('start');
  L('cwd ok');
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  L('created doc');
  Board := PCBServer.GetCurrentPCBBoard;
  if Board = Nil then L('board nil') else L('board ok: ' + Board.FileName);
  T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
  T.X1 := MilsToCoord(1000); T.Y1 := MilsToCoord(1000);
  T.X2 := MilsToCoord(2000); T.Y2 := MilsToCoord(1000);
  T.Width := MilsToCoord(10);
  T.Layer := eTopLayer;
  Board.AddPCBObject(T);
  L('track added');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p01.PcbDoc');
  Doc.DoFileSave('PCB');
  L('saved');
end;
